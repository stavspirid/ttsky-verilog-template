/*
 * tt_mem_bridge.v
 *
 * Bridges the 8-bit parallel bus from the FPGA (ui_in) to the
 * tinyqv_mem_ctrl signals on the TT chip side. A 16-phase counter
 * is shared with fpga_mem_bridge on the FPGA: both sides reset
 * together so the schedule stays in lock-step.
 */

`default_nettype none

module tt_mem_bridge (
    input  wire        clk,
    input  wire        rstn,

    input  wire  [7:0] from_fpga,     // ui_in  (FPGA drives)
    output reg   [7:0] to_fpga,       // uo_out (TT drives)

    // ---- tinyqv_mem_ctrl: instruction fetch ----
    output reg  [23:1] instr_addr,
    output reg         instr_fetch_restart,
    output reg         instr_fetch_stall,
    input  wire        instr_fetch_started,
    input  wire        instr_fetch_stopped,
    input  wire [15:0] instr_data,
    input  wire        instr_ready,

    // ---- tinyqv_mem_ctrl: data bus ----
    output reg  [24:0] data_addr,
    output reg   [1:0] data_write_n,
    output reg   [1:0] data_read_n,
    output reg  [31:0] data_to_write,
    output reg         data_continue,
    input  wire        data_ready,
    input  wire [31:0] data_from_read
);

    // -------------------------------------------------------
    // 16-phase counter
    // -------------------------------------------------------
    reg [3:0] phase;
    always @(posedge clk) begin
        if (!rstn) phase <= 4'd0;
        else       phase <= phase + 4'd1;
    end

    // -------------------------------------------------------
    // RECEIVE FROM FPGA — latch each field at its phase
    // -------------------------------------------------------
    always @(posedge clk) begin
        if (!rstn) begin
            instr_fetch_restart <= 1'b0;
            instr_fetch_stall   <= 1'b0;
            instr_addr          <= 23'd0;
            data_addr           <= 25'd0;
            data_write_n        <= 2'b11;
            data_read_n         <= 2'b11;
            data_continue       <= 1'b0;
            data_to_write       <= 32'd0;
        end else begin
            case (phase)
                4'd0: begin
                    instr_fetch_restart <= from_fpga[7];
                    instr_fetch_stall   <= from_fpga[6];
                    instr_addr[23:18]   <= from_fpga[5:0];
                end
                4'd1: instr_addr[17:10] <= from_fpga;
                4'd2: instr_addr[9:2]   <= from_fpga;
                4'd3: begin
                    instr_addr[1]    <= from_fpga[7];
                    data_addr[24:18] <= from_fpga[6:0];
                end
                4'd4: data_addr[17:10] <= from_fpga;
                4'd5: data_addr[9:2]   <= from_fpga;
                4'd6: begin
                    data_addr[1:0] <= from_fpga[7:6];
                    data_write_n   <= from_fpga[5:4];
                    data_read_n    <= from_fpga[3:2];
                    data_continue  <= from_fpga[1];
                    // from_fpga[0] spare
                end
                4'd8:  data_to_write[7:0]   <= from_fpga;
                4'd9:  data_to_write[15:8]  <= from_fpga;
                4'd10: data_to_write[23:16] <= from_fpga;
                4'd11: data_to_write[31:24] <= from_fpga;
                default: ;
            endcase
        end
    end

    // -------------------------------------------------------
    // LATCH SINGLE-CYCLE PULSES from the memory controller
    // (held until they have been transmitted to the FPGA, then cleared)
    // -------------------------------------------------------
    reg        started_l, stopped_l, instr_ready_l, data_ready_l;
    reg [15:0] instr_data_l;
    reg [31:0] data_from_read_l;

    always @(posedge clk) begin
        if (!rstn) begin
            started_l        <= 1'b0;
            stopped_l        <= 1'b0;
            instr_ready_l    <= 1'b0;
            data_ready_l     <= 1'b0;
            instr_data_l     <= 16'd0;
            data_from_read_l <= 32'd0;
        end else begin
            if (instr_fetch_started) started_l <= 1'b1;
            if (instr_fetch_stopped) stopped_l <= 1'b1;
            if (instr_ready) begin
                instr_ready_l <= 1'b1;
                instr_data_l  <= instr_data;
            end
            if (data_ready) begin
                data_ready_l     <= 1'b1;
                data_from_read_l <= data_from_read;
            end
            // Clear after status byte has been sent (phase 13 → tx at 14)
            if (phase == 4'd13) begin
                started_l     <= instr_fetch_started;
                stopped_l     <= instr_fetch_stopped;
                instr_ready_l <= instr_ready;
                data_ready_l  <= data_ready;
                if (instr_ready) instr_data_l     <= instr_data;
                if (data_ready)  data_from_read_l <= data_from_read;
            end
        end
    end

    // -------------------------------------------------------
    // SEND TO FPGA — registered output, one byte per phase
    // -------------------------------------------------------
    always @(posedge clk) begin
        case (phase)
            4'd14: to_fpga <= {started_l     | instr_fetch_started,
                               stopped_l     | instr_fetch_stopped,
                               instr_ready_l | instr_ready,
                               data_ready_l  | data_ready,
                               4'b0000};
            4'd15: to_fpga <= instr_data_l[7:0];
            4'd0:  to_fpga <= instr_data_l[15:8];
            4'd1:  to_fpga <= data_from_read_l[7:0];
            4'd2:  to_fpga <= data_from_read_l[15:8];
            4'd3:  to_fpga <= data_from_read_l[23:16];
            4'd4:  to_fpga <= data_from_read_l[31:24];
            default: to_fpga <= 8'h00;
        endcase
    end

endmodule
