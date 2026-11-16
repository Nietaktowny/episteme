from datetime import datetime

import pytest
from pydantic import ValidationError

from episteme.models.errors import ErrorInfo


def capture(error: BaseException) -> ErrorInfo:
    try:
        raise error
    except BaseException as caught:
        return ErrorInfo.from_exception(caught)


class TestFromException:
    def test_name_is_the_exception_class_name(self):
        assert capture(FileNotFoundError("gone")).name == "FileNotFoundError"

    def test_name_of_a_custom_exception(self):
        class SyncBroke(RuntimeError):
            pass

        assert capture(SyncBroke("x")).name == "SyncBroke"

    def test_message(self):
        assert capture(ValueError("bad value")).message == "bad value"

    def test_empty_message(self):
        assert capture(ValueError()).message == ""

    def test_unicode_message(self):
        assert capture(ValueError("Zażółć 🚀")).message == "Zażółć 🚀"

    def test_traceback_is_formatted_text(self):
        info = capture(ValueError("bad value"))
        assert info.traceback.startswith("Traceback (most recent call last):")
        assert info.traceback.splitlines()[-1] == "ValueError: bad value"

    def test_traceback_shows_where_it_happened(self):
        def deep():
            raise ValueError("deep")

        try:
            deep()
        except ValueError as error:
            info = ErrorInfo.from_exception(error)
        assert "in deep" in info.traceback
        assert "test_models_errors.py" in info.traceback

    def test_chained_causes_are_included(self):
        try:
            try:
                raise KeyError("inner")
            except KeyError as inner:
                raise RuntimeError("outer") from inner
        except RuntimeError as error:
            info = ErrorInfo.from_exception(error)
        assert "KeyError: 'inner'" in info.traceback
        assert "direct cause" in info.traceback
        assert info.name == "RuntimeError"

    def test_exception_that_was_never_raised_has_a_traceback_line(self):
        info = ErrorInfo.from_exception(ValueError("never raised"))
        assert info.traceback.strip().endswith("ValueError: never raised")

    def test_any_base_exception(self):
        assert capture(KeyboardInterrupt()).name == "KeyboardInterrupt"

    def test_records_when_it_was_captured(self):
        before = datetime.now()
        info = capture(ValueError("x"))
        assert before <= info.occurred_at <= datetime.now()


class TestModel:
    def test_traceback_is_not_in_the_repr(self):
        info = capture(ValueError("secret detail"))
        assert "Traceback" not in repr(info)
        assert "ValueError" in repr(info)

    def test_is_frozen(self):
        with pytest.raises(ValidationError):
            capture(ValueError("x")).name = "other"

    def test_can_be_built_directly(self):
        info = ErrorInfo(name="OSError", message="disk full", traceback="")
        assert (info.name, info.message, info.traceback) == ("OSError", "disk full", "")

    @pytest.mark.parametrize("missing", ["name", "message", "traceback"])
    def test_required_fields(self, missing: str):
        values = {"name": "E", "message": "m", "traceback": "t"}
        del values[missing]
        with pytest.raises(ValidationError):
            ErrorInfo(**values)

    def test_survives_a_json_round_trip(self):
        info = capture(ValueError("round trip"))
        assert ErrorInfo.model_validate_json(info.model_dump_json()) == info


class TestRich:
    def error(self, message: str = "disk full") -> ErrorInfo:
        try:
            raise OSError(message)
        except OSError as caught:
            return ErrorInfo.from_exception(caught)

    def test_returns_a_panel(self):
        from rich.panel import Panel

        assert isinstance(self.error().__rich__(), Panel)

    def test_shows_name_message_time_and_traceback(self, render):
        info = self.error()
        text = render(info)
        assert "OSError" in text
        assert "disk full" in text
        assert f"{info.occurred_at:%Y-%m-%d %H:%M:%S}" in text
        assert "Traceback (most recent call last)" in text

    def test_markup_in_the_message_is_not_interpreted(self, render):
        text = render(self.error("could not write [a.md] and [bold]x[/bold]"))
        assert "could not write [a.md] and [bold]x[/bold]" in text

    def test_markup_in_the_name_is_not_interpreted(self, render):
        info = ErrorInfo(name="[red]Odd[/red]", message="m", traceback="")
        assert "[red]Odd[/red]" in render(info)

    def test_empty_message_is_labelled(self, render):
        assert "(no message)" in render(ErrorInfo(name="E", message="", traceback=""))

    def test_no_traceback_section_when_there_is_none(self, render):
        assert "Traceback" not in render(ErrorInfo(name="E", message="m", traceback=""))

    def test_long_lines_wrap_inside_a_narrow_console(self, render):
        text = render(self.error("word " * 60), width=40)
        assert all(len(line) <= 40 for line in text.splitlines())
        assert text.count("word") == 120  # nothing lost: the message, and again in the traceback's last line

    def test_chained_causes_are_visible(self, render):
        try:
            try:
                raise KeyError("inner")
            except KeyError as inner:
                raise RuntimeError("outer") from inner
        except RuntimeError as error:
            text = render(ErrorInfo.from_exception(error))
        assert "KeyError" in text and "direct cause" in text
