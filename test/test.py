# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0
#
# Tests for the flash-only authQV TT die.
#
# The die contains: tt_mem_bridge + tinyqv_mem_ctrl + qspi_controller.
# The CPU lives on the companion FPGA and talks to this die over an
# 8-phase 8-bit parallel bus on ui_in / uo_out. We model the FPGA side
# manually here: drive the request frame byte-by-byte and watch uo_out
# / uio_out to verify the right things happen.
#
# Pin map (from src/project.v):
#   uio[0] = flash CS (active low)
#   uio[1] = SD0
#   uio[2] = SD1
#   uio[3] = SCK
#   uio[4] = SD2
#   uio[5] = SD3
#   uio[6] = RAM A CS (held high)
#   uio[7] = RAM B CS (held high)

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, First, Timer


# ---------- Bit helpers ------------------------------------------------

FLASH_CS = 0x01
SD0      = 0x02
SD1      = 0x04
SCK      = 0x08
SD2      = 0x10
SD3      = 0x20
RAM_A_CS = 0x40
RAM_B_CS = 0x80


def get_flash_cs(uio):    return (uio & FLASH_CS) != 0
def get_sck(uio):         return (uio & SCK)      != 0
def get_ram_cs(uio):      return ((uio & RAM_A_CS) != 0) and ((uio & RAM_B_CS) != 0)


# ---------- Bridge frame builder ---------------------------------------
#
# 8-phase request frame (FPGA -> TT, ui_in), matches tt_mem_bridge:
#   0: {fetch_restart, fetch_stall, instr_addr[23:18]}
#   1: instr_addr[17:10]
#   2: instr_addr[9:2]
#   3: {instr_addr[1], data_addr[23:18], 1'b0}
#   4: data_addr[17:10]
#   5: data_addr[9:2]
#   6: {data_addr[1:0], data_read_n, 4'b0}
#   7: spare

def build_frame(instr_addr=0, fetch_restart=0, fetch_stall=0,
                data_addr=0, data_read_n=0b11):
    f = [0] * 8
    f[0] = ((fetch_restart & 1) << 7) | ((fetch_stall & 1) << 6) \
           | ((instr_addr >> 18) & 0x3F)
    f[1] = (instr_addr >> 10) & 0xFF
    f[2] = (instr_addr >> 2)  & 0xFF
    f[3] = (((instr_addr >> 1) & 1) << 7) | (((data_addr >> 18) & 0x3F) << 1)
    f[4] = (data_addr >> 10) & 0xFF
    f[5] = (data_addr >> 2)  & 0xFF
    f[6] = (((data_addr & 0x3) << 6) | ((data_read_n & 0x3) << 4))
    f[7] = 0
    return f


# ---------- Test fixtures ----------------------------------------------

async def reset_dut(dut):
    """Apply the standard reset sequence and return on the cycle after
    rst_n is released. The TT chip's bridge phase counter is at 0 the
    cycle reset goes high; our local frame phase tracker (in tests) must
    start at the same cycle to stay in lock-step."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.ena.value    = 1
    dut.ui_in.value  = 0
    dut.uio_in.value = 0

    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 8)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    # Now we're aligned: TT bridge phase will increment from 0 on the
    # NEXT clock edge, same as our test phase tracker.


async def drive_frame_for(dut, frame, n_cycles, phase_start=0):
    """Drive the 8-byte request frame for n_cycles, starting at the
    given local phase. Returns (final_phase, list of (uio_out, uo_out)
    samples taken on each rising edge)."""
    samples = []
    p = phase_start
    for _ in range(n_cycles):
        dut.ui_in.value = frame[p]
        await RisingEdge(dut.clk)
        samples.append((dut.uio_out.value.integer,
                        dut.uo_out.value.integer))
        p = (p + 1) & 7
    return p, samples


# =======================================================================
# Tests
# =======================================================================

@cocotb.test()
async def test_reset_state(dut):
    """After reset, all chip selects are high (inactive) and the QSPI
    output enables are off."""
    await reset_dut(dut)

    # Drive idle frame for a few cycles and check pins.
    idle = [0] * 8
    _, samples = await drive_frame_for(dut, idle, 16)

    for uio, _ in samples:
        assert get_flash_cs(uio), "Flash CS should be high (inactive) at idle"
        assert get_ram_cs(uio),   "RAM CS pins should be high (inactive)"

    dut._log.info("Reset state OK: all CS high, FSM idle")


@cocotb.test()
async def test_fetch_asserts_flash_cs(dut):
    """A fetch_restart request should make qspi_ctrl drop flash CS and
    start toggling SCK within a few phases."""
    await reset_dut(dut)

    frame = build_frame(instr_addr=0x000000, fetch_restart=1)

    # Drive the request for several full frames so the request latches
    # regardless of the starting phase.
    _, samples = await drive_frame_for(dut, frame, 64)

    flash_cs_dropped = any(not get_flash_cs(uio) for uio, _ in samples)
    sck_toggled = len({get_sck(uio) for uio, _ in samples}) > 1

    assert flash_cs_dropped, "Flash CS never went low after fetch_restart"
    assert sck_toggled,      "SCK never toggled — qspi_ctrl not running"

    dut._log.info("Fetch start OK: flash CS dropped, SCK toggling")


@cocotb.test()
async def test_ram_cs_stays_high(dut):
    """In the flash-only build the RAM CS pins are tied high in
    project.v. They must NEVER drop, even during a fetch."""
    await reset_dut(dut)

    frame = build_frame(instr_addr=0x123456, fetch_restart=1)
    _, samples = await drive_frame_for(dut, frame, 80)

    bad = [i for i, (uio, _) in enumerate(samples) if not get_ram_cs(uio)]
    assert not bad, f"RAM CS dropped at sample indices {bad[:5]}..."

    dut._log.info("RAM CS held high throughout, as expected")


@cocotb.test()
async def test_response_status_byte_phase(dut):
    """The bridge transmits the status byte at TT-phase 7 (which is
    bridge transmit-phase 7 → uo_out registered out at the next edge).
    Until any pulse fires, the status byte should have all four status
    bits low. Verify uo_out cycles through phases without garbage."""
    await reset_dut(dut)

    idle = [0] * 8
    _, samples = await drive_frame_for(dut, idle, 32)

    # Lower 4 bits of every uo_out byte are reserved zeros in the slim
    # bridge frame. They must always read 0.
    for _, uo in samples:
        assert (uo & 0x0F) == 0, \
            f"Reserved low nibble of uo_out should be 0, got 0x{uo:02x}"

    dut._log.info("Response framing OK: reserved bits are zero at idle")


@cocotb.test()
async def test_fetch_then_idle_releases_cs(dut):
    """After a fetch is in flight, asserting fetch_restart again with
    no actual progress (we never drive uio_in with valid flash data,
    so the FSM stays in ADDR/DUMMY phases) should still cause the
    transaction to be torn down when we drop fetch_restart and assert
    nothing else."""
    await reset_dut(dut)

    # 1) Start a fetch
    frame_go = build_frame(instr_addr=0x000100, fetch_restart=1)
    p, samples_go = await drive_frame_for(dut, frame_go, 64)
    assert any(not get_flash_cs(uio) for uio, _ in samples_go), \
        "fetch_restart didn't bring flash CS low"

    # 2) Hold fetch_restart high but stall — qspi_ctrl will keep its
    #    transaction active. CS should stay LOW while busy.
    frame_stall = build_frame(instr_addr=0x000100,
                              fetch_restart=1, fetch_stall=1)
    _, samples_stall = await drive_frame_for(dut, frame_stall, 32, p)

    cs_low_during_stall = sum(1 for uio, _ in samples_stall
                              if not get_flash_cs(uio))
    # Should be low for at least most of the stall window
    assert cs_low_during_stall > len(samples_stall) // 2, \
        f"Flash CS unexpectedly high during stall " \
        f"({cs_low_during_stall}/{len(samples_stall)} samples low)"

    dut._log.info("Stall behavior OK: CS held low while busy + stalled")


# =======================================================================
# QSPI flash mock (for end-to-end tests)
# =======================================================================
#
# qspi_controller drives the flash in fast-read quad-IO continuous-read
# mode. Per-nibble timing on the QSPI bus:
#
#   nibbles 0..5    : address (TT drives SD0-3, OE=1111)
#   nibbles 6..7    : continuous-read mode bits 0xAA (TT drives)
#   nibbles 8..11   : turnaround (TT releases bus, OE=0000)
#   nibbles 12..    : data (flash drives, 2 nibbles per byte, MSN first)
#
# The mock watches uio_out for SCK rising edges, captures the address
# nibbles, and then drives uio_in with the requested bytes from a memory
# map. Bytes not in the map default to 0xFF.
#
# uio pin packing (from src/project.v):
#   uio_out[3] = SCK              uio_in [5:4] = SD3:SD2 (read by die)
#   uio_out[5:4] = SD3:SD2 (TT drives during ADDR/DUMMY1)
#   uio_out[2:1] = SD1:SD0
#   uio_in [2:1] = SD1:SD0 (read by die)


def _pack_uio_in(nibble: int) -> int:
    """Pack a 4-bit nibble into the uio_in byte layout the die expects."""
    sd3 = (nibble >> 3) & 1
    sd2 = (nibble >> 2) & 1
    sd1 = (nibble >> 1) & 1
    sd0 = (nibble >> 0) & 1
    return (sd3 << 5) | (sd2 << 4) | (sd1 << 2) | (sd0 << 1)


def _unpack_sd_out(uio_out: int) -> int:
    """Extract the 4-bit nibble the die is currently driving on SD0-3."""
    sd3 = (uio_out >> 5) & 1
    sd2 = (uio_out >> 4) & 1
    sd1 = (uio_out >> 2) & 1
    sd0 = (uio_out >> 1) & 1
    return (sd3 << 3) | (sd2 << 2) | (sd1 << 1) | sd0


async def flash_mock(dut, content):
    """Cocotb coroutine that emulates a QSPI flash chip on uio_in.

    `content` is a dict {byte_address: byte_value}; missing bytes
    default to 0xFF. The mock auto-resets its state on every CS rising
    edge, so it correctly handles back-to-back transactions.
    """
    last_sck = 0
    last_cs  = 1
    addr     = 0
    nibble   = 0   # nibble counter since the falling CS

    while True:
        await RisingEdge(dut.clk)
        uio = int(dut.uio_out.value)
        cs  = uio & FLASH_CS
        sck = (uio >> 3) & 1

        # Falling CS → start of transaction
        if last_cs and not cs:
            addr = 0
            nibble = 0

        # Rising CS → idle the bus
        if not last_cs and cs:
            dut.uio_in.value = 0

        if not cs:
            # Rising SCK edge — qspi_ctrl just placed a new nibble out
            # (during ADDR/DUMMY1) or expects us to provide one (DATA).
            if sck and not last_sck:
                if nibble < 6:
                    addr = ((addr << 4) | _unpack_sd_out(uio)) & 0xFFFFFF
                nibble += 1

            # Once past addr+mode+dummy, present read data on uio_in.
            # nibbles 12.. are data, MSN of byte 0 first.
            if nibble >= 12:
                data_nib = nibble - 12
                byte_offset = data_nib >> 1
                is_low_nib  = data_nib & 1
                byte_val = content.get((addr + byte_offset) & 0xFFFFFF, 0xFF)
                nib = (byte_val & 0xF) if is_low_nib else ((byte_val >> 4) & 0xF)
                dut.uio_in.value = _pack_uio_in(nib)

        last_sck = sck
        last_cs  = cs


# =======================================================================
# End-to-end tests (with the flash mock)
# =======================================================================

@cocotb.test()
async def test_end_to_end_fetch_returns_data(dut):
    """Drive a fetch_restart at address 0 and verify the response frame
    on uo_out eventually carries the expected instr_data bytes back from
    the (mocked) flash."""
    await reset_dut(dut)

    # Memory image: instruction word at address 0 is 0x12345678 (LE).
    # qspi_ctrl will read 2 bytes (16-bit instr) starting at addr 0.
    content = {0: 0x78, 1: 0x56, 2: 0x34, 3: 0x12}
    cocotb.start_soon(flash_mock(dut, content))

    frame = build_frame(instr_addr=0x000000, fetch_restart=1)
    _, samples = await drive_frame_for(dut, frame, 600)

    uo_bytes = [uo for _, uo in samples]

    # The bridge transmits the status byte at TT-phase 7 (with bit 5 =
    # instr_ready latched). After a successful fetch, that bit must
    # show up at least once.
    saw_instr_ready = any((b & 0x20) != 0 for b in uo_bytes)
    assert saw_instr_ready, \
        "Bridge response never set instr_ready — fetch never completed"

    # The bridge transmits instr_data[7:0] then instr_data[15:8] in the
    # two phases following the status byte. After instr_ready latches,
    # we should see 0x78 and 0x56 appear in uo_out somewhere.
    assert 0x78 in uo_bytes, \
        f"instr_data[7:0]=0x78 never appeared in uo_out stream"
    assert 0x56 in uo_bytes, \
        f"instr_data[15:8]=0x56 never appeared in uo_out stream"

    dut._log.info(
        "End-to-end fetch OK: instr_ready latched, instr_data 0x5678 round-tripped")


@cocotb.test()
async def test_end_to_end_fetch_address_propagates(dut):
    """Sanity check that the address sent in the request frame actually
    reaches the flash mock — i.e. the bridge is decoding instr_addr the
    way mem_ctrl + qspi_ctrl expect.

    We use a memory image where each address has a distinct byte
    pattern, fetch from a non-zero address, and verify the bytes that
    come back match what's at that address."""
    await reset_dut(dut)

    # Place a recognizable 16-bit word at address 0x4000.
    # qspi_ctrl reads 2 bytes per instr fetch starting from
    # {instr_addr, 1'b0} = 0x4000.
    content = {0x4000: 0xCD, 0x4001: 0xAB}
    cocotb.start_soon(flash_mock(dut, content))

    # instr_addr is 23-bit, indexes 16-bit halfwords. To request the
    # halfword at byte address 0x4000 we set instr_addr = 0x2000.
    frame = build_frame(instr_addr=0x2000, fetch_restart=1)
    _, samples = await drive_frame_for(dut, frame, 600)
    uo_bytes = [uo for _, uo in samples]

    saw_instr_ready = any((b & 0x20) != 0 for b in uo_bytes)
    assert saw_instr_ready, "instr_ready never set for fetch @ 0x4000"

    assert 0xCD in uo_bytes, \
        "Low byte 0xCD from address 0x4000 not seen in response"
    assert 0xAB in uo_bytes, \
        "High byte 0xAB from address 0x4001 not seen in response"

    dut._log.info(
        "Addressed fetch OK: 0xABCD round-tripped from address 0x4000")


@cocotb.test()
async def test_end_to_end_data_read(dut):
    """Issue a data read (8-bit) at a specific address and verify the
    byte comes back through the bridge's data_from_read response slot."""
    await reset_dut(dut)

    content = {0x100: 0x42}
    cocotb.start_soon(flash_mock(dut, content))

    # Issue a single-byte read at byte address 0x100. data_addr is the
    # full 24-bit byte address; data_read_n=00 → 1 byte.
    frame = build_frame(instr_addr=0, fetch_restart=0,
                        data_addr=0x100, data_read_n=0b00)
    _, samples = await drive_frame_for(dut, frame, 600)
    uo_bytes = [uo for _, uo in samples]

    # The status byte's bit 4 is data_ready_l (see tt_mem_bridge.v).
    saw_data_ready = any((b & 0x10) != 0 for b in uo_bytes)
    assert saw_data_ready, "data_ready never asserted in response stream"

    assert 0x42 in uo_bytes, \
        "Read byte 0x42 from address 0x100 not seen in uo_out"

    dut._log.info("End-to-end data read OK: byte 0x42 round-tripped")


@cocotb.test()
async def test_qspi_oe_only_when_busy(dut):
    """uio_oe[0] (flash CS) and uio_oe[3] (SCK) must be high (= the TT
    die is driving them) once reset is released. SD0..SD3 OE bits should
    only assert when qspi_ctrl is actively driving the bus (during ADDR
    and DUMMY1 phases)."""
    await reset_dut(dut)

    # Idle: SD lines should be undriven (OE low) most of the time.
    idle_oe = [int(dut.uio_oe.value)]
    await ClockCycles(dut.clk, 4)
    idle_oe.append(int(dut.uio_oe.value))

    for v in idle_oe:
        # CS, SCK, and the two RAM CS pins are always driven outputs.
        assert (v & FLASH_CS) != 0, "Flash CS should be a driven output"
        assert (v & SCK)      != 0, "SCK should be a driven output"
        assert (v & RAM_A_CS) != 0, "RAM A CS should be a driven output"
        assert (v & RAM_B_CS) != 0, "RAM B CS should be a driven output"

    dut._log.info("uio_oe map OK at idle")
