import logging
import re
from rich.logging import RichHandler

class RedactingFilter(logging.Filter):
    # masque des motifs fréquents dans le message final
    _patterns = [
        (re.compile(r"\bAC[a-fA-F0-9]{32}\b"), "AC****"),
        (re.compile(r"\bSK[a-fA-F0-9]{32}\b"), "SK****"),
        (re.compile(r"\bPN[a-fA-F0-9]{32}\b"), "PN****"),
        (re.compile(r"\bAD[a-fA-F0-9]{32}\b"), "AD****"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat, repl in self._patterns:
            msg = pat.sub(repl, msg)
        # on réinjecte le message “nettoyé”
        record.msg = msg
        record.args = ()
        return True

def configure_cli_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO

    handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_level=True,
        show_path=False,
        markup=True,
    )
    handler.addFilter(RedactingFilter())

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[handler],
    )
