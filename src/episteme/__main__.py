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
    print(episteme.sync_obsidian())
    

if __name__ == "__main__":
    main()
    