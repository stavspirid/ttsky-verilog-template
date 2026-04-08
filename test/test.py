# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0
#
# Test for the flash-only TT die. The CPU now lives on the FPGA and
# talks to this die via an 8-phase 8-bit parallel bus on ui_in / uo_out.
# We drive that protocol manually and verify that the QSPI memory
# controller responds (flash CS asserted, SCK toggling).

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles


def build_frame(instr_addr=0, fetch_restart=0, fetch_stall=0,
                data_addr=0, data_read_n=0b11):
    """Build the 8-byte request frame that fpga_mem_bridge would send."""
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
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    # Let the phase counter come out of reset cleanly.
    await ClockCycles(dut.clk, 4)

    # ----- Issue an instruction-fetch request via the bridge -----
    # Drive a fetch_restart frame for several full cycles so the
    # request latches even if we missed the first window.
    frame = build_frame(instr_addr=0x000000, fetch_restart=1)
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
        "Flash CS never asserted — bridge -> mem_ctrl path is broken"
    assert sck_toggled, \
        "QSPI SCK never toggled — qspi_ctrl is not running"

    dut._log.info("Bridge round-trip OK: flash CS asserted, SCK toggling")
