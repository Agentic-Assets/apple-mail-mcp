"""On-disk receipts for bounded mailbox and exact-id exports."""

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.core import normalize_message_ids

# AppleScript renders large integers in scientific notation when coerced
# to text. Keep plan and filename IDs in the same decimal form as selectors.
EXPORT_ID_TEXT_HANDLER = """on exportIdText(messageId)
    if messageId < 1000000 then return messageId as text
    set leadingDigits to messageId div 1000000
    set trailingDigits to "000000" & ((messageId mod 1000000) as integer)
    return (leadingDigits as text) & text -6 thru -1 of trailingDigits
end exportIdText"""


@dataclass(frozen=True)
class ExportCheckpoint:
    directory: Path
    format: str
    include_attachments: bool
    requested_ids: list[str] | None = None
    batch: str = field(default_factory=lambda: f"batch-{uuid4().hex}")

    @property
    def path(self) -> Path:
        return self.directory / self.batch

    def _planned_ids(self) -> list[str] | None:
        if self.requested_ids is not None:
            return self.requested_ids
        try:
            return normalize_message_ids((self.path / "plan.ids").read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeError):
            return None

    def _completed(self, planned: list[str]) -> dict[str, Path]:
        completed: dict[str, Path] = {}
        try:
            for entry in self.path.iterdir():
                message_id = entry.name.partition("_")[0]
                if message_id not in planned:
                    continue
                output = entry / "message.eml" if self.include_attachments else entry
                if output.suffix == f".{self.format}" and output.is_file() and output.stat().st_size:
                    completed[message_id] = output
        except OSError:
            pass  # Keep verified entries, leaving unreadable entries pending.
        return completed

    def timeout_result(
        self,
        *,
        account: str,
        mailbox: str,
        offset: int | None = None,
        prior_batches: list[str] | None = None,
        unstarted: list[tuple[str, list[str]]] | None = None,
    ) -> str:
        planned = self._planned_ids()
        completed = self._completed(planned or [])
        pending = [message_id for message_id in planned if message_id not in completed] if planned is not None else None
        if planned is None:
            next_step = "The page was not recorded. Retry the same offset with a smaller batch before advancing."
        elif pending:
            next_step = "Recheck the checkpoint, then retry any still-pending IDs as message_ids in the same mailbox."
        else:
            next_step = "All planned message files landed. Review the checkpoint before advancing."
        return serialize_tool_error(
            ToolError(
                code="EXPORT_BATCH_TIMEOUT",
                message="AppleScript timed out during export. Completed entries confirm message files only.",
                remediation={
                    "account": account,
                    "mailbox": mailbox,
                    "offset": offset,
                    "batch": self.batch,
                    "checkpoint": str(self.path),
                    "planned_message_ids": planned,
                    "completed": [
                        {"message_id": message_id, "path": str(completed[message_id])}
                        for message_id in (planned or [])
                        if message_id in completed
                    ],
                    "pending_message_ids": pending,
                    "attachment_bundle_unverified": self.include_attachments,
                    "prior_batch_results": prior_batches or [],
                    "unstarted_batches": [{"mailbox": name, "message_ids": ids} for name, ids in (unstarted or [])],
                    "next_step": next_step,
                },
            )
        )


def checkpoint_script(batch: str) -> str:
    return f"""set exportDir to exportDir & "/{batch}"
                do shell script "/bin/mkdir -m 700 " & quoted form of exportDir"""


def record_plan_script(ids_var: str) -> str:
    return f"""set AppleScript's text item delimiters to linefeed
                set planText to {ids_var} as text
                set AppleScript's text item delimiters to ""
                set planPath to exportDir & "/plan.ids"
                set planStaging to planPath & ".partial"
                do shell script "/usr/bin/printf %s " & quoted form of planText & " > " & quoted form of planStaging & " && /bin/mv -f " & quoted form of planStaging & " " & quoted form of planPath"""


def atomic_export_write_script(*, shared_directory: bool = False) -> str:
    staging_name = f".message-{uuid4().hex}.partial" if shared_directory else "message.partial"
    publish_command = '"/bin/mv -f "'
    if shared_directory:
        # A pre-existing directory at the published path must not absorb the
        # staging file and make mv report a successful message export.
        publish_command = '"/bin/test ! -d " & quoted form of filePath & " && /bin/mv -f "'
    return f"""if exportContent is missing value or exportContent is "" then error "Mail returned no message source"
                set stagingPath to exportDir & "/{staging_name}"
                set fileRef to open for access POSIX file stagingPath with write permission
                set eof of fileRef to 0
                write exportContent to fileRef as «class utf8»
                close access fileRef
                set fileRef to missing value
                do shell script {publish_command} & quoted form of stagingPath & " " & quoted form of filePath"""
