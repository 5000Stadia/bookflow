"""Lightweight console bootstrap; optional diagnostics begin before CLI imports."""

import sys

from bookflow.core import performance


def main() -> None:
    recorder = performance.configure_from_env()
    try:
        if recorder is not None:
            # Conservative safety observation, not argument parsing: protect every
            # spelled root even when Click later rejects the invocation. No values
            # are retained in events and no CLI errors or option precedence change.
            for index, argument in enumerate(sys.argv[1:], 1):
                if argument == "--data-root" and index + 1 < len(sys.argv):
                    performance.protect_selection(sys.argv[index + 1])
                elif argument.startswith("--data-root="):
                    performance.protect_selection(argument.partition("=")[2])
        with performance.span("cli.import_app"):
            from bookflow.adapters.cli.app import main as cli_main
        with performance.span("cli.invoke"):
            cli_main()
    finally:
        if recorder is not None:
            recorder.close()


if __name__ == "__main__":
    main()
