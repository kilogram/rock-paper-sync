"""Terminal output and formatting utilities.

Provides ANSI color codes and colored output formatting for test harness.
"""


class Colors:
    """ANSI color codes for terminal output."""

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    END = "\033[0m"
    BOLD = "\033[1m"


def print_ok(msg: str) -> None:
    """Print success message in green.

    Args:
        msg: Success message
    """
    print(f"{Colors.GREEN}  {msg}{Colors.END}")


def print_info(msg: str) -> None:
    """Print info message in blue.

    Args:
        msg: Info message
    """
    print(f"{Colors.BLUE}  {msg}{Colors.END}")


def print_warn(msg: str) -> None:
    """Print warning message in yellow.

    Args:
        msg: Warning message
    """
    print(f"{Colors.YELLOW}  {msg}{Colors.END}")


def print_error(msg: str) -> None:
    """Print error message in red.

    Args:
        msg: Error message
    """
    print(f"{Colors.RED}  {msg}{Colors.END}")


def print_header(title: str) -> None:
    """Print section header with top and bottom borders.

    Args:
        title: Header title
    """
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.HEADER}{title.center(60)}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.HEADER}{'=' * 60}{Colors.END}\n")


def print_subheader(title: str) -> None:
    """Print subsection header with borders.

    Args:
        title: Subheader title
    """
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'-' * 40}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{title}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'-' * 40}{Colors.END}")
