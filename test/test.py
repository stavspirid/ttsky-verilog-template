# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0
#
# Test for the bridge-only TT die. The CPU now lives on the FPGA
# and talks to this die via a 16-phase 8-bit parallel bus on
# ui_in / uo_out. We drive that protocol manually and verify the
# QSPI memory controller responds (flash CS asserted, SCK toggling).

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles


# 16-phase request frame: list of 16 bytes, one per phase, that
# fpga_mem_bridge would send. Built to match tt_mem_bridge's RX
# schedule exactly.
def build_frame(instr_addr=0, fetch_restart=0, fetch_stall=0,
                data_addr=0, data_write_n=0b11, data_read_n=0b11,
                data_continue=0, data_to_write=0):
    f = [0] * 16
    f[0]  = ((fetch_restart & 1) << 7) | ((fetch_stall & 1) << 6) \
            | ((instr_addr >> 18) & 0x3F)
    f[1]  = (instr_addr >> 10) & 0xFF
    f[2]  = (instr_addr >> 2)  & 0xFF
    f[3]  = (((instr_addr >> 1) & 1) << 7) | ((data_addr >> 18) & 0x7F)
    f[4]  = (data_addr >> 10) & 0xFF
    f[5]  = (data_addr >> 2)  & 0xFF
    f[6]  = (((data_addr & 0x3) << 6)
             | ((data_write_n & 0x3) << 4)
             | ((data_read_n  & 0x3) << 2)
             | ((data_continue & 1) << 1))
    f[8]  = (data_to_write >> 0)  & 0xFF
    f[9]  = (data_to_write >> 8)  & 0xFF
    f[10] = (data_to_write >> 16) & 0xFF
    f[11] = (data_to_write >> 24) & 0xFF
    return f


@cocotb.test()
async def test_bridge_fetch(dut):
    dut._log.info("Starting authQV bridge testbench")

    # 50 MHz clock (matches CLOCK_PERIOD = 20 ns in config.json)
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())

    dut.ena.value    = 1
    dut.ui_in.value  = 0
    dut.uio_in.value = 0

    # ----- Reset -----
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    dut.rst_n.value = 0

    # During reset, qspi_ctrl latches its config straps from uio_in.
    # Set RAM CS bits high (inactive) and a sane latency value.
    dut.uio_in.value = 0xC5
    await ClockCycles(dut.clk, 10)
    dut.uio_in.value = 0x00
    dut.rst_n.value  = 1

    # Let the phase counter come out of reset cleanly.
    await ClockCycles(dut.clk, 4)

    # ----- Issue an instruction-fetch request via the bridge -----
    # We need to send a frame whose phase-0 byte arrives at the TT
    # die exactly when its internal `phase` counter is 0. Both phase
    # counters start at 0 the first cycle rstn is high, so we just
    # align our drive to the next rising edge after sync.
    frame = build_frame(instr_addr=0x000000, fetch_restart=1)

    # Drive the same frame for several full cycles so the request
    # latches even if we missed the first window.
    for _ in range(4):
        for byte in frame:
            dut.ui_in.value = byte
            await RisingEdge(dut.clk)

    # Now hold a stalled-but-active request and watch the QSPI bus.
    frame = build_frame(instr_addr=0x000000, fetch_restart=0,
                        fetch_stall=0)
    flash_cs_seen_low = False
    sck_toggled = False
    last_sck = (dut.uio_out.value.integer >> 3) & 1

    for _ in range(8):
        for byte in frame:
            dut.ui_in.value = byte
            await RisingEdge(dut.clk)
            uio = dut.uio_out.value.integer
            if (uio & 0x01) == 0:
                flash_cs_seen_low = True
            sck = (uio >> 3) & 1
            if sck != last_sck:
                sck_toggled = True
            last_sck = sck

    assert flash_cs_seen_low, \
        "Flash CS never asserted — bridge→mem_ctrl path is broken"
    assert sck_toggled, \
        "QSPI SCK never toggled — qspi_ctrl is not running"

    dut._log.info("Bridge round-trip OK: flash CS asserted, SCK toggling")
