import logging

from rich import print

from episteme import Episteme

logger = logging.getLogger("episteme")

def main():
    from rich.logging import RichHandler

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)]
    )
    
    episteme = Episteme()
    daily_note = episteme.obsidian["2026-11-04"]
    print(daily_note)
    print(daily_note.frontmatter)
    print(daily_note)

if __name__ == "__main__":
    main()
    