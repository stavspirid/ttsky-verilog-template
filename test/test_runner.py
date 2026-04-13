import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

@cocotb.test()
async def check_reset(dut):
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.rst_n.value = 0
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    
    dut.rst_n.value = 1
    
    for _ in range(5):
        await RisingEdge(dut.clk)
        dut._log.info(f"phase inside tt_mem_bridge: {dut.user_project.i_bridge.phase.value.to_unsigned()}")
