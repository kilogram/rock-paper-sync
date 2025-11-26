"""User interaction utilities for device testing.

Provides functions for prompting user actions during device tests.
"""

from .output import Colors


def user_prompt(
    title: str,
    steps: list[str],
    allow_quit: bool = True,
) -> bool:
    """Prompt user to perform actions on device.

    Displays a formatted prompt with numbered steps and waits for user
    confirmation.

    Args:
        title: Title of the action
        steps: List of steps for user to perform
        allow_quit: Whether 'q' quits the test

    Returns:
        True if user confirmed, False if user quit

    Example:
        if not user_prompt("Add annotations", [
            "Open document on reMarkable",
            "Highlight some text",
            "Wait for cloud sync",
        ]):
            return False
    """
    print(f"\n{Colors.BOLD}{Colors.YELLOW}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.YELLOW}USER ACTION REQUIRED{Colors.END}")
    print(f"{Colors.BOLD}{Colors.YELLOW}{'=' * 60}{Colors.END}\n")
    print(f"{Colors.BOLD}{title}{Colors.END}\n")

    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")

    if allow_quit:
        print(f"\n{Colors.YELLOW}Press Enter when done, or 'q' to quit...{Colors.END}")
    else:
        print(f"\n{Colors.YELLOW}Press Enter when done...{Colors.END}")

    response = input().strip().lower()

    if allow_quit and response == "q":
        return False

    return True


def user_confirm(
    question: str,
    default: bool = True,
) -> bool:
    """Ask user a yes/no question.

    Args:
        question: Question to ask
        default: Default answer if user just presses Enter

    Returns:
        True for yes, False for no

    Example:
        if user_confirm("Continue with next test?"):
            ...
    """
    if default:
        prompt = f"{Colors.YELLOW}{question} [Y/n]: {Colors.END}"
    else:
        prompt = f"{Colors.YELLOW}{question} [y/N]: {Colors.END}"

    response = input(prompt).strip().lower()

    if not response:
        return default

    return response in ("y", "yes")


def user_input(
    prompt: str,
    default: str | None = None,
) -> str:
    """Get text input from user.

    Args:
        prompt: Prompt text
        default: Default value if user presses Enter

    Returns:
        User input or default

    Example:
        text = user_input("Enter expected text:", default="hello")
    """
    if default:
        full_prompt = f"{Colors.YELLOW}{prompt} [{default}]: {Colors.END}"
    else:
        full_prompt = f"{Colors.YELLOW}{prompt}: {Colors.END}"

    response = input(full_prompt).strip()

    if not response and default:
        return default

    return response


def display_info(
    title: str,
    items: dict[str, str],
) -> None:
    """Display information in a formatted box.

    Args:
        title: Box title
        items: Key-value pairs to display

    Example:
        display_info("Test Configuration", {
            "Workspace": "/path/to/workspace",
            "Device Folder": "TestFolder",
        })
    """
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'-' * 40}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{title}{Colors.END}")
    print(f"{Colors.BLUE}{'-' * 40}{Colors.END}")

    max_key_len = max(len(k) for k in items.keys())
    for key, value in items.items():
        print(f"  {Colors.CYAN}{key:>{max_key_len}}{Colors.END}: {value}")

    print()


def display_results(
    results: list[tuple[str, bool]],
) -> None:
    """Display test results summary.

    Args:
        results: List of (test_name, success) tuples

    Example:
        display_results([
            ("annotation-roundtrip", True),
            ("ocr-recognition", False),
        ])
    """
    print(f"\n{Colors.BOLD}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{'TEST RESULTS'.center(60)}{Colors.END}")
    print(f"{'=' * 60}\n")

    passed = sum(1 for _, s in results if s)
    total = len(results)

    for name, success in results:
        status = f"{Colors.GREEN}PASS{Colors.END}" if success else f"{Colors.RED}FAIL{Colors.END}"
        print(f"  {name}: {status}")

    print(f"\n{Colors.BOLD}{passed}/{total} passed{Colors.END}")

    if passed == total:
        print(f"\n{Colors.GREEN}All tests passed!{Colors.END}")
    else:
        print(f"\n{Colors.RED}{total - passed} test(s) failed{Colors.END}")
