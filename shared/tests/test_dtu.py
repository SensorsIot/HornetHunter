"""DTU provisioning tests (FSD §12): diff-only writes, guaranteed AT+EXIT."""

from __future__ import annotations

import pytest

from hornethunter_shared.dtu import params_from_config, provision


class FakeSerial:
    """Scripts DTU AT replies from a `param -> current value` table."""

    def __init__(
        self, values: dict[str, str], *, enter: str = "OK", raise_on: str | None = None
    ) -> None:
        self.values = dict(values)
        self.enter = enter
        self.raise_on = raise_on
        self.writes: list[str] = []
        self._pending = b""

    def reset_input_buffer(self) -> None:
        return None

    def write(self, data: bytes) -> None:
        text = data.decode("ascii").strip()
        self.writes.append(text)
        if self.raise_on and self.raise_on in text:
            raise OSError("scripted write failure")
        self._pending = self._respond(text)

    def read_all(self) -> bytes:
        out, self._pending = self._pending, b""
        return out

    def _respond(self, text: str) -> bytes:
        if text == "+++":
            return self.enter.encode()
        if text.startswith("AT+") and text.endswith("?"):
            name = text[3:-1]
            # The real DTU echoes the query first, then the value — the shape that
            # broke naive parsing on hardware (§12).
            return f"{text}\r\n+{name}={self.values.get(name, '')}\r\nOK\r\n".encode()
        if text.startswith("AT+") and "=" in text:
            name, _, value = text[3:].partition("=")
            self.values[name] = value
            return b"OK\r\n"
        return b"OK\r\n"


def _written(serial: FakeSerial) -> list[str]:
    return [w for w in serial.writes if w.startswith("AT+") and "=" in w]


def test_writes_only_differing_parameters() -> None:
    serial = FakeSerial({"MODE": "1", "SF": "7", "ADDR": "5"})
    result = provision(serial, {"MODE": "1", "SF": "9", "ADDR": "5"})

    assert result.entered is True
    assert result.written == {"SF": "9"}  # MODE and ADDR already matched
    assert _written(serial) == ["AT+SF=9"]
    assert not result.mismatches
    assert "AT+EXIT" in serial.writes


def test_at_exit_guaranteed_when_a_write_raises() -> None:
    serial = FakeSerial({"MODE": "1", "SF": "7"}, raise_on="AT+SF=")
    with pytest.raises(OSError, match="scripted write failure"):
        provision(serial, {"SF": "9"})
    # FR-11.5: AT+EXIT is issued on the exception path too.
    assert "AT+EXIT" in serial.writes


def test_provisioning_is_idempotent() -> None:
    serial = FakeSerial({"MODE": "1", "SF": "9"})
    first = provision(serial, {"MODE": "1", "SF": "9"})
    second = provision(serial, {"MODE": "1", "SF": "9"})
    assert first.written == {}
    assert second.written == {}


def test_matching_params_write_nothing_when_the_dtu_echoes() -> None:
    # Regression (§12): the DTU echoes the query (`AT+MODE?\r\n+MODE=1\r\nOK`). A parser
    # that grabs the echoed line sees every value as "AT+MODE?", rewrites everything,
    # and reports false mismatches. With correct parsing an all-matching pass is a
    # true no-op — exactly what live hardware must show.
    serial = FakeSerial({"MODE": "1", "ADDR": "1", "TXCH": "24", "SF": "7", "BW": "0"})
    result = provision(
        serial, {"MODE": "1", "ADDR": "1", "TXCH": "24", "SF": "7", "BW": "0"}
    )
    assert result.written == {}
    assert result.mismatches == {}
    assert _written(serial) == []  # not a single AT+X=... write was issued


def test_at_mode_not_entered_leaves_dtu_untouched() -> None:
    serial = FakeSerial({"SF": "7"}, enter="")  # +++ gets no response
    result = provision(serial, {"SF": "9"})
    assert result.entered is False
    assert _written(serial) == []  # nothing written when AT mode was never entered


def test_write_only_key_is_recorded_unverifiable() -> None:
    serial = FakeSerial({})
    result = provision(serial, {"KEY": "0123456789abcdef"})
    assert result.written == {"KEY": "0123456789abcdef"}
    assert result.unverifiable == ("KEY",)
    # AT+KEY is queried once to diff, but never re-read to verify (§12.3):
    # a verified parameter would show two `AT+KEY?` reads (diff + read-back).
    assert serial.writes.count("AT+KEY?") == 1


def test_params_from_config_forces_mode_and_address() -> None:
    # An empty [dtu] table still forces the two settings that break the link when
    # wrong: Stream mode and the node's own address.
    params = params_from_config({}, address=1)
    assert params == {"MODE": "1", "ADDR": "1"}


def test_params_from_config_master_broadcast_address() -> None:
    params = params_from_config({"channel": 24}, address=0xFFFF)
    assert params["ADDR"] == "65535"  # 0xFFFF broadcast-monitor (§19.2)
    assert params["TXCH"] == "24" and params["RXCH"] == "24"  # channel → both


def test_params_from_config_maps_radio_parameters() -> None:
    params = params_from_config(
        {"channel": 24, "sf": 7, "bw": 0, "cr": 1, "power": 22, "netid": 5},
        address=2,
    )
    assert params == {
        "MODE": "1",
        "ADDR": "2",
        "TXCH": "24",
        "RXCH": "24",
        "SF": "7",
        "BW": "0",
        "CR": "1",
        "PWR": "22",
        "NETID": "5",
    }


def test_params_from_config_end_to_end_provisions_a_packet_mode_stick() -> None:
    # A swapped stick in Packet mode with the wrong address self-corrects: provision
    # writes only MODE and ADDR (channel already matches), never touching the rest.
    serial = FakeSerial({"MODE": "2", "ADDR": "4", "TXCH": "24", "RXCH": "24"})
    result = provision(serial, params_from_config({"channel": 24}, address=1))
    assert result.written == {"MODE": "1", "ADDR": "1"}
    assert not result.mismatches
    assert "AT+EXIT" in serial.writes
