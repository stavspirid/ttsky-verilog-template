import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

# ── Pin constants ──────────────────────────────────────────────────────────────
# uio_out layout (project.v):
#   uio_out = {2'b11, qspi_data[3:2], SCK, qspi_data[1:0], CS}
#   [0] = flash CS      (active-low)
#   [1] = QSPI SD0/IO0
#   [2] = QSPI SD1/IO1
#   [3] = SCK
#   [4] = QSPI SD2/IO2
#   [5] = QSPI SD3/IO3
#   [7:6] = held 1 (unused PSRAM CS pins)
CS_BIT  = 0
SD0_BIT = 1
SD1_BIT = 2
SCK_BIT = 3
SD2_BIT = 4
SD3_BIT = 5

# Hold fetch_stall=1 between frames so no spontaneous fetch is triggered.
# Bit 6 = fetch_stall, bit 7 = fetch_restart (0), remaining bits = addr (0).
SAFE_IDLE = 0x40

# ── Pin helpers ────────────────────────────────────────────────────────────────

def get_cs(dut):
    return (int(dut.uio_out.value) >> CS_BIT) & 1

def get_sck(dut):
    return (int(dut.uio_out.value) >> SCK_BIT) & 1

def get_qspi_nibble(dut):
    """Reconstruct the 4-bit nibble driven on the four QSPI IO lines."""
    u = int(dut.uio_out.value)
    return (((u >> SD3_BIT) & 1) << 3 |
            ((u >> SD2_BIT) & 1) << 2 |
            ((u >> SD1_BIT) & 1) << 1 |
            ((u >> SD0_BIT) & 1))

# ── Frame builder ──────────────────────────────────────────────────────────────

def build_frame(fetch_restart=0, fetch_stall=0, instr_addr=0,
                data_addr=0, data_read_n=3):
    """
    Build the 8-byte request frame for the tt_mem_bridge parallel bus.

    Phase layout (tt_mem_bridge.v):
      0: {fetch_restart[7], fetch_stall[6], instr_addr[23:18]}
      1: instr_addr[17:10]
      2: instr_addr[9:2]
      3: {instr_addr[1], data_addr[23:18], 1'b0}
      4: data_addr[17:10]
      5: data_addr[9:2]
      6: {data_addr[1:0], data_read_n[1:0], 4'b0}
      7: spare (SAFE_IDLE)
    """
    return [
        (fetch_restart << 7) | (fetch_stall << 6) | ((instr_addr >> 18) & 0x3F),
        (instr_addr >> 10) & 0xFF,
        (instr_addr >>  2) & 0xFF,
        (((instr_addr >> 1) & 1) << 7) | (((data_addr >> 18) & 0x3F) << 1),
        (data_addr >> 10) & 0xFF,
        (data_addr >>  2) & 0xFF,
        ((data_addr & 0x03) << 6) | ((data_read_n & 0x03) << 4),
        SAFE_IDLE,
    ]

# ── Setup ──────────────────────────────────────────────────────────────────────

async def setup_dut(dut):
    """
    Start the clock, reset the DUT, and return aligned to phase 0.

    The TT die uses a negedge-registered reset (project.v):
        always @(negedge clk) rst_reg_n <= rst_n;
    so the internal phase counter sees the release one half-cycle after
    rst_n goes high.  We hold reset for 8 cycles, release, then drain one
    complete 8-phase idle frame so the caller is at a clean phase-0 boundary.

    Phase-0 alignment proof:
      - rst_n released after posedge N  → rst_reg_n goes high at negedge N
      - posedge N+1: rstn=1, phase was 0 (reset forced it) → phase ticks to 1
        but the bridge *processes* from_fpga with phase=0 at this edge  ✓
      - 8 subsequent cycles complete one full frame (phases 0-7)
      - On return: next posedge will be phase 0 again
    """
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.rst_n.value  = 0
    dut.ena.value    = 1
    dut.ui_in.value  = SAFE_IDLE
    dut.uio_in.value = 0xFF      # QSPI data lines pulled high (no flash)
    await ClockCycles(dut.clk, 8)
    dut.rst_n.value = 1
    await send_frame(dut, build_frame(fetch_restart=0, fetch_stall=1))  # drain one idle frame → back to phase 0

async def send_frame(dut, frame):
    """
    Drive one 8-phase request frame on ui_in.
    Must be called at a phase-0 boundary (immediately after setup_dut
    or immediately after a previous send_frame call).
    """
    for byte in frame:
        dut.ui_in.value = byte
        await RisingEdge(dut.clk)
    dut.ui_in.value = SAFE_IDLE

# ── Tests ──────────────────────────────────────────────────────────────────────

@cocotb.test()
async def test_reset_pin_state(dut):
    """
    BLACK-BOX: External pins must be in safe states during and after reset.

    Checked via uio_oe / uio_out only — no internal signal access.

    Contracts:
      During reset  (rst_n=0): uio_oe == 0x00  (all bidirectional pins inputs)
      After  reset  (rst_n=1): CS  == 1         (flash deselected)
                               SCK == 0         (clock idle)
    """
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.rst_n.value  = 0
    dut.ena.value    = 1
    dut.ui_in.value  = SAFE_IDLE
    dut.uio_in.value = 0xFF
    await ClockCycles(dut.clk, 4)

    assert int(dut.uio_oe.value) == 0x00, (
        f"uio_oe must be 0x00 during reset, got 0x{int(dut.uio_oe.value):02X}")

    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    assert get_cs(dut)  == 1, "Flash CS must be high (inactive) after reset"
    assert get_sck(dut) == 0, "SCK must be low (idle) after reset"
    dut._log.info("PASS: reset state — uio_oe=0x00 during reset, CS=1 SCK=0 after")


@cocotb.test()
async def test_idle_no_qspi_activity(dut):
    """
    BLACK-BOX: fetch_stall=1 must keep the QSPI bus completely silent.

    Drives 4 consecutive frames with fetch_restart=0, fetch_stall=1 and
    asserts that CS and SCK never change from their idle states.
    """
    await setup_dut(dut)

    idle_frame = build_frame(fetch_restart=0, fetch_stall=1)

    for frame_idx in range(4):
        for phase, byte in enumerate(idle_frame):
            dut.ui_in.value = byte
            await RisingEdge(dut.clk)
            assert get_cs(dut)  == 1, (
                f"CS went low during idle frame {frame_idx}, phase {phase}")
            assert get_sck(dut) == 0, (
                f"SCK toggled during idle frame {frame_idx}, phase {phase}")

    dut.ui_in.value = SAFE_IDLE
    dut._log.info("PASS: 4 idle frames — CS and SCK stayed silent throughout")


@cocotb.test()
async def test_fetch_restart_asserts_cs(dut):
    """
    BLACK-BOX: A fetch_restart request must pull flash CS low.

    Sends one frame with fetch_restart=1 and waits up to 20 cycles for
    uio_out[0] (CS) to go low.  No internal signals are read.
    """
    await setup_dut(dut)

    frame = build_frame(fetch_restart=1, fetch_stall=0, instr_addr=0x000000)
    await send_frame(dut, frame)

    cs_asserted = False
    for _ in range(20):
        await RisingEdge(dut.clk)
        if get_cs(dut) == 0:
            cs_asserted = True
            break

    assert cs_asserted, (
        "Flash CS did not go low within 20 cycles after fetch_restart=1")
    dut._log.info("PASS: fetch_restart — CS asserted low")


@cocotb.test()
async def test_sck_toggles_after_cs(dut):
    """
    BLACK-BOX: After CS is asserted, SCK must start toggling.

    The QSPI controller begins clocking out the address immediately after
    CS goes low.  We require at least 4 SCK transitions within 40 cycles.
    """
    await setup_dut(dut)

    frame = build_frame(fetch_restart=1, fetch_stall=0, instr_addr=0x000000)
    await send_frame(dut, frame)

    # Wait for CS low
    for _ in range(20):
        await RisingEdge(dut.clk)
        if get_cs(dut) == 0:
            break
    else:
        assert False, "CS never went low — cannot test SCK"

    # Count SCK transitions
    transitions = 0
    prev_sck = get_sck(dut)
    for _ in range(40):
        await RisingEdge(dut.clk)
        curr_sck = get_sck(dut)
        if curr_sck != prev_sck:
            transitions += 1
        prev_sck = curr_sck
        if transitions >= 4:
            break

    assert transitions >= 4, (
        f"Expected ≥4 SCK transitions after CS low, got {transitions}")
    dut._log.info(f"PASS: SCK toggled {transitions} times after CS assertion")


@cocotb.test()
async def test_qspi_address_encoding(dut):
    """
    BLACK-BOX: The correct 24-bit address must appear on the QSPI data lines.

    Sends fetch_restart=1 for address 0xABCDEE (halfword-aligned, bit 0 = 0).
    Captures nibbles on uio_out's QSPI IO lines at each SCK rising edge and
    verifies:
      nibbles[0:6] == [0xA, 0xB, 0xC, 0xD, 0xE, 0xE]  (24-bit address MSB-first)
      nibbles[6:8] == [0xA, 0xA]                        (continuous-read mode 4'b1010)

    SCK timing (qspi_ctrl.v, Mode 0):
      Data changes on SCK falling edge; flash (and this test) samples on rising.
      In Verilog: spi_clk_pos register toggles every system clock; the address
      shift-register advances when spi_clk_pos WAS 1 (i.e. on the falling edge),
      so reading uio_out immediately after a rising edge gives the current nibble.

    Nibble reconstruction from uio_out:
      uio_out[5]=SD3, uio_out[4]=SD2, uio_out[2]=SD1, uio_out[1]=SD0
      nibble = (SD3<<3)|(SD2<<2)|(SD1<<1)|SD0
    """
    await setup_dut(dut)

    TEST_ADDR = 0xABCDEE   # even byte address → halfword-aligned ✓

    frame = build_frame(fetch_restart=1, fetch_stall=0, instr_addr=TEST_ADDR)
    await send_frame(dut, frame)

    # Wait for CS low (start of QSPI transaction)
    for _ in range(20):
        await RisingEdge(dut.clk)
        if get_cs(dut) == 0:
            break
    else:
        assert False, "CS never went low — cannot capture address nibbles"

    # Capture nibbles at each SCK rising edge
    nibbles   = []
    prev_sck  = get_sck(dut)
    for _ in range(200):
        await RisingEdge(dut.clk)
        curr_sck = get_sck(dut)
        if prev_sck == 0 and curr_sck == 1:   # SCK rising edge
            nibbles.append(get_qspi_nibble(dut))
        prev_sck = curr_sck
        if len(nibbles) >= 8:
            break

    assert len(nibbles) >= 6, (
        f"Only captured {len(nibbles)} nibbles before timeout; need ≥6")

    expected_addr = [
        (TEST_ADDR >> 20) & 0xF,   # 0xA
        (TEST_ADDR >> 16) & 0xF,   # 0xB
        (TEST_ADDR >> 12) & 0xF,   # 0xC
        (TEST_ADDR >>  8) & 0xF,   # 0xD
        (TEST_ADDR >>  4) & 0xF,   # 0xE
        (TEST_ADDR >>  0) & 0xF,   # 0xE
    ]
    assert nibbles[:6] == expected_addr, (
        f"Address nibbles wrong.\n"
        f"  expected: {[hex(n) for n in expected_addr]}\n"
        f"  got:      {[hex(n) for n in nibbles[:6]]}")

    if len(nibbles) >= 8:
        assert nibbles[6:8] == [0xA, 0xA], (
            f"Mode bits wrong. Expected [0xA, 0xA], "
            f"got {[hex(n) for n in nibbles[6:8]]}")

    dut._log.info(
        f"PASS: QSPI address nibbles: {[hex(n) for n in nibbles[:8]]}")


@cocotb.test()
async def test_response_frame_status_byte(dut):
    """
    BLACK-BOX: uo_out must produce a well-formed status byte each frame.

    The bridge drives uo_out with a status byte at the phase-7→0 transition:
        uo_out = {fetch_started[7], fetch_stopped[6],
                  instr_ready[5],   data_ready[4],   4'b0}

    After reset with no flash traffic the lower nibble must always be 0x0
    (reserved by the bridge protocol).  This holds for both RTL and GLS.
    """
    await setup_dut(dut)

    # Drive one idle frame; the status byte is registered at the last
    # (phase-7) clock edge of the frame and is visible on uo_out right after.
    idle_frame = build_frame(fetch_restart=0, fetch_stall=1)
    await send_frame(dut, idle_frame)

    # uo_out now holds the status byte (set at phase 7 of the frame we just sent)
    status = int(dut.uo_out.value)

    assert (status & 0x0F) == 0x0, (
        f"Lower nibble of status byte must be 0 (reserved), "
        f"got 0x{status:02X}")

    dut._log.info(
        f"PASS: status byte 0x{status:02X} — "
        f"started={( status>>7)&1} stopped={(status>>6)&1} "
        f"instr_ready={(status>>5)&1} data_ready={(status>>4)&1}")
