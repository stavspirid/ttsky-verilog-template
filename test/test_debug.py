import cocotb
from cocotb.triggers import RisingEdge
from test import setup_dut, build_frame, send_frame

@cocotb.test()
async def test_debug(dut):
    await setup_dut(dut)
    TEST_ADDR = 0xABCDEE
    frame = build_frame(fetch_restart=1, fetch_stall=0, instr_addr=TEST_ADDR)
    
    for i, byte in enumerate(frame):
        dut.ui_in.value = byte
        await RisingEdge(dut.clk)
        phase = dut.user_project.i_bridge.phase.value.binstr
        addr_in = dut.user_project.i_mem.addr_in.value.binstr
        fsm = dut.user_project.i_mem.q_ctrl.fsm_state.value.binstr
        addr = dut.user_project.i_mem.q_ctrl.addr.value.binstr
        dut._log.info(f"Edge {i+1}: phase={phase}, ui_in={byte:02x}, addr_in={addr_in}, fsm={fsm}, addr={addr}")

    for i in range(10):
        await RisingEdge(dut.clk)
        phase = dut.user_project.i_bridge.phase.value.binstr
        addr_in = dut.user_project.i_mem.addr_in.value.binstr
        fsm = dut.user_project.i_mem.q_ctrl.fsm_state.value.binstr
        addr = dut.user_project.i_mem.q_ctrl.addr.value.binstr
        dut._log.info(f"Post-Edge {i+1}: phase={phase}, addr_in={addr_in}, fsm={fsm}, addr={addr}")

