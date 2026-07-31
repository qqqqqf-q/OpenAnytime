"""在 Mac 本地执行官方血糖算法库(libalgorithm_jni_1_29_0_0.so,arm64)。

为什么存在:官方 App 显示的血糖不是传感器原始电流,而是这个闭源 native 库
的输出(GLU_MG/18)。库内部状态复杂(多级 EMA、滑动窗口、温度补偿、
限速),静态还原未完成;但算法本身可以用 Unicorn CPU 模拟器原样执行——
这不是拟合/近似,是逐条执行官方指令。已与手机上 adb 执行同一 .so 的结果
交叉验证:566/566 点 GLU_MG 完全一致(2026-07-31)。

用法:
    from openanytime.native_emulator import compute_official_glucose
    glu_mg = compute_official_glucose(records)  # {glucose_id: mg/dL int}

输入记录必须按 glucose_id 从 0 连续排列(算法有跨点状态,断点会污染
后续所有输出);缺洞由调用方用邻近值填补后再传入。

库路径:环境变量 OPENANYTIME_NATIVE_LIB,默认
~/Library/Application Support/cgm-data/native/libalgorithm_jni_1_29_0_0.so
(.so 闭源不入 git,从 APK 提取一次即可)。
"""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence

import lief
from unicorn import (
    UC_ARCH_ARM64,
    UC_ERR_INSN_INVALID,
    UC_HOOK_CODE,
    UC_HOOK_INSN,
    UC_HOOK_INSN_INVALID,
    UC_HOOK_MEM_FETCH_UNMAPPED,
    UC_HOOK_MEM_READ_UNMAPPED,
    UC_HOOK_MEM_WRITE_UNMAPPED,
    UC_PROT_ALL,
    UC_PROT_EXEC,
    UC_PROT_READ,
    UC_PROT_WRITE,
    Uc,
    UcError,
    arm64_const,
)

DEFAULT_LIB_PATH = (
    Path.home()
    / "Library/Application Support/cgm-data/native/libalgorithm_jni_1_29_0_0.so"
)

# 模拟器地址空间布局(BASE 以下是镜像加载区,其余为自管内存)
BASE = 0x1000_0000
STACK_TOP = 0x2000_0000
STACK_SIZE = 0x10_0000
HEAP_BASE = 0x3000_0000
HEAP_SIZE = 0x10_0000
TLS_BASE = 0x4000_0000
TLS_SIZE = 0x1000
HOOK_BASE = 0x5000_0000
HOOK_SIZE = 0x10000

# .so 内关键地址(手工 ELF 解析 + Capstone 反汇编定位,2026-07-31)
ADDR_CGM_ALGORITHM = 0x820B4
ADDR_OUTPUT_STRUCT = 0x185B78  # 全局输出结构,x0 传入

# 算法配置(来自 jadx:EDevice.DEVICEANY,CT5/Anytime 固件协议)
WARMUP_POINTS = 15  # initNumber,之前为预热期
LIFE_POINTS = 7695  # endNumber
ALGORITHM_ID = 11  # Anytime 设备算法编号
K_DEFAULT = 1.11  # 传感器校准系数 k
R_DEFAULT = 1.0


def _align_down(value: int, alignment: int) -> int:
    return value & ~(alignment - 1)


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


@dataclass(frozen=True)
class SensorReading:
    """算法单点输入。ib 在本传感器上恒为 0(全量历史实测)。"""

    glucose_id: int
    iw: float
    ib: float
    temperature_c: float


class NativeAlgorithmError(RuntimeError):
    """模拟器执行失败(镜像损坏、未处理指令、内存错误)。"""


class _Emulator:
    """加载 .so、补重定位、桩掉外部 libc 调用的 arm64 执行环境。

    外部符号处理策略:数学函数(exp/sin)用 Python 等价实现;
    rand 用确定性 LCG(算法只用它做扰动,真值不受影响——已验证);
    time 返回固定值;正则相关桩为不匹配;其余未实现符号直接抛错,
    宁可失败也不返回编造值。
    """

    def __init__(self, lib_path: Path) -> None:
        if not lib_path.is_file():
            raise NativeAlgorithmError(f"native library not found: {lib_path}")
        self.binary = lief.parse(str(lib_path))
        self.uc = Uc(UC_ARCH_ARM64, 0)
        self._sym_by_name: Dict[str, lief.ELF.Symbol] = {}
        self._import_hooks: Dict[int, str] = {}
        self._heap_offset = 0
        self._hook_counter = 0
        self._rand_seed = 0
        self._load_segments()
        self._apply_relocations()
        self._setup_memory()
        self._install_hooks()

    def _load_segments(self) -> None:
        for seg in self.binary.segments:
            if seg.type != lief.ELF.Segment.TYPE.LOAD:
                continue
            va = _align_down(seg.virtual_address, 0x1000)
            vs = _align_up(seg.virtual_size, 0x1000)
            addr = BASE + va
            prot = 0
            if seg.has(lief.ELF.Segment.FLAGS.R):
                prot |= UC_PROT_READ
            if seg.has(lief.ELF.Segment.FLAGS.W):
                prot |= UC_PROT_WRITE
            if seg.has(lief.ELF.Segment.FLAGS.X):
                prot |= UC_PROT_EXEC
            self.uc.mem_map(addr, vs, prot)
            data = bytes(seg.content)
            if data:
                self.uc.mem_write(addr, bytes(vs))
                self.uc.mem_write(BASE + seg.virtual_address, data)

    def _apply_relocations(self) -> None:
        for symbol in self.binary.dynamic_symbols:
            self._sym_by_name[symbol.name] = symbol

        def sym_addr(name: str) -> int:
            symbol = self._sym_by_name.get(name)
            if symbol is None:
                raise NativeAlgorithmError(f"undefined symbol {name!r}")
            return BASE + symbol.value

        for reloc in self.binary.dynamic_relocations:
            addr = BASE + reloc.address
            if reloc.type == lief.ELF.Relocation.TYPE.AARCH64_RELATIVE:
                value = BASE + reloc.addend
            elif reloc.type in (
                lief.ELF.Relocation.TYPE.AARCH64_ABS64,
                lief.ELF.Relocation.TYPE.AARCH64_GLOB_DAT,
            ):
                value = sym_addr(reloc.symbol.name)
            else:
                raise NativeAlgorithmError(
                    f"unhandled dynamic reloc {reloc.type} at {hex(reloc.address)}"
                )
            self.uc.mem_write(addr, struct.pack("<Q", value))

        for reloc in self.binary.pltgot_relocations:
            addr = BASE + reloc.address
            name = reloc.symbol.name
            if name in self._sym_by_name and self._sym_by_name[name].value != 0:
                value = BASE + self._sym_by_name[name].value
            else:
                value = self._alloc_import_hook(name)
            self.uc.mem_write(addr, struct.pack("<Q", value))

    def _alloc_import_hook(self, name: str) -> int:
        pc = HOOK_BASE + self._hook_counter * 4
        self._hook_counter += 1
        self._import_hooks[pc] = name
        return pc

    def _setup_memory(self) -> None:
        self.uc.mem_map(
            STACK_TOP - STACK_SIZE, STACK_SIZE, UC_PROT_READ | UC_PROT_WRITE
        )
        self.uc.reg_write(arm64_const.UC_ARM64_REG_SP, STACK_TOP - 0x10)
        self.uc.mem_map(HEAP_BASE, HEAP_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(TLS_BASE, TLS_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.reg_write(arm64_const.UC_ARM64_REG_TPIDR_EL0, TLS_BASE)
        self.uc.mem_write(TLS_BASE + 0x28, struct.pack("<Q", 0xDEADBEEFCAFEBABE))
        self.uc.mem_map(HOOK_BASE, HOOK_SIZE, UC_PROT_EXEC | UC_PROT_READ)
        self.uc.mem_write(HOOK_BASE, b"\x00\x00\x00\x00" * (HOOK_SIZE // 4))
        # 调用 CGM_algorithm 时的哨兵返回地址:执行到这里就停止模拟
        self._sentinel = HOOK_BASE + HOOK_SIZE - 4
        self._import_hooks[self._sentinel] = "__return_sentinel"

    def _install_hooks(self) -> None:
        self.uc.hook_add(UC_HOOK_INSN_INVALID, self._handle_invalid)
        self.uc.hook_add(
            UC_HOOK_MEM_READ_UNMAPPED
            | UC_HOOK_MEM_WRITE_UNMAPPED
            | UC_HOOK_MEM_FETCH_UNMAPPED,
            self._handle_mem_invalid,
        )
        self.uc.hook_add(
            UC_HOOK_INSN, self._handle_mrs, None, 1, 0, arm64_const.UC_ARM64_INS_MRS
        )
        self.uc.hook_add(
            UC_HOOK_CODE, self._handle_code, None, HOOK_BASE, HOOK_BASE + HOOK_SIZE - 1
        )

    def _handle_code(self, uc: Uc, address: int, size: int, user_data) -> None:
        if address in self._import_hooks:
            self._dispatch_import(self._import_hooks[address])
            lr = uc.reg_read(arm64_const.UC_ARM64_REG_LR)
            uc.reg_write(arm64_const.UC_ARM64_REG_PC, lr)

    def _handle_mrs(self, uc: Uc, reg: int, cp_reg, user_data) -> int:
        # 只处理 mrs Xt, tpidr_el0(读 TLS 基址)
        if (
            cp_reg.op0 == 3
            and cp_reg.op1 == 3
            and cp_reg.crn == 13
            and cp_reg.crm == 0
            and cp_reg.op2 == 2
        ):
            uc.reg_write(reg, TLS_BASE)
            return TLS_BASE
        raise UcError(UC_ERR_INSN_INVALID, f"unhandled mrs cp_reg={cp_reg}")

    def _handle_mem_invalid(
        self, uc: Uc, access: int, address: int, size: int, value: int, user_data
    ) -> bool:
        pc = uc.reg_read(arm64_const.UC_ARM64_REG_PC)
        raise NativeAlgorithmError(
            f"unmapped memory access addr={hex(address)} pc={hex(pc)}"
        )

    def _handle_invalid(self, uc: Uc, user_data) -> bool:
        pc = uc.reg_read(arm64_const.UC_ARM64_REG_PC)
        if pc not in self._import_hooks:
            raise UcError(UC_ERR_INSN_INVALID, f"invalid insn at {hex(pc)}")
        self._dispatch_import(self._import_hooks[pc])
        lr = uc.reg_read(arm64_const.UC_ARM64_REG_LR)
        uc.reg_write(arm64_const.UC_ARM64_REG_PC, lr)
        return True

    def _dispatch_import(self, name: str) -> None:
        uc = self.uc
        if name == "__return_sentinel":
            uc.emu_stop()
        elif name in ("exp",):
            d0 = uc.reg_read(arm64_const.UC_ARM64_REG_D0)
            val = math.exp(struct.unpack("<d", struct.pack("<Q", d0))[0])
            uc.reg_write(
                arm64_const.UC_ARM64_REG_D0,
                struct.unpack("<Q", struct.pack("<d", val))[0],
            )
        elif name in ("expf",):
            s0 = uc.reg_read(arm64_const.UC_ARM64_REG_S0)
            val = math.exp(struct.unpack("<f", struct.pack("<I", s0))[0])
            uc.reg_write(
                arm64_const.UC_ARM64_REG_S0,
                struct.unpack("<I", struct.pack("<f", float(val)))[0],
            )
        elif name in ("sinf",):
            s0 = uc.reg_read(arm64_const.UC_ARM64_REG_S0)
            val = math.sin(struct.unpack("<f", struct.pack("<I", s0))[0])
            uc.reg_write(
                arm64_const.UC_ARM64_REG_S0,
                struct.unpack("<I", struct.pack("<f", float(val)))[0],
            )
        elif name in ("srand",):
            self._rand_seed = uc.reg_read(arm64_const.UC_ARM64_REG_X0) & 0xFFFFFFFF
        elif name in ("rand",):
            self._rand_seed = (1103515245 * self._rand_seed + 12345) & 0x7FFFFFFF
            uc.reg_write(arm64_const.UC_ARM64_REG_X0, self._rand_seed)
        elif name in ("time",):
            uc.reg_write(arm64_const.UC_ARM64_REG_X0, 0x64C2E000)
        elif name == "calloc":
            n = uc.reg_read(arm64_const.UC_ARM64_REG_X0)
            sz = uc.reg_read(arm64_const.UC_ARM64_REG_X1)
            uc.reg_write(arm64_const.UC_ARM64_REG_X0, self._alloc_heap(n * sz))
        elif name == "free":
            pass
        elif name == "memset":
            ptr = uc.reg_read(arm64_const.UC_ARM64_REG_X0)
            val = uc.reg_read(arm64_const.UC_ARM64_REG_X1) & 0xFF
            n = uc.reg_read(arm64_const.UC_ARM64_REG_X2)
            uc.mem_write(ptr, bytes([val]) * n)
            uc.reg_write(arm64_const.UC_ARM64_REG_X0, ptr)
        elif name == "memmove":
            dst = uc.reg_read(arm64_const.UC_ARM64_REG_X0)
            src = uc.reg_read(arm64_const.UC_ARM64_REG_X1)
            n = uc.reg_read(arm64_const.UC_ARM64_REG_X2)
            uc.mem_write(dst, bytes(uc.mem_read(src, n)))
            uc.reg_write(arm64_const.UC_ARM64_REG_X0, dst)
        elif name == "strlen":
            ptr = uc.reg_read(arm64_const.UC_ARM64_REG_X0)
            data = bytes(uc.mem_read(ptr, 1024))
            length = data.find(b"\x00")
            uc.reg_write(
                arm64_const.UC_ARM64_REG_X0,
                length if length >= 0 else len(data),
            )
        elif name in ("regcomp",):
            uc.reg_write(arm64_const.UC_ARM64_REG_X0, 0)
        elif name in ("regexec",):
            uc.reg_write(arm64_const.UC_ARM64_REG_X0, 1)  # REG_NOMATCH
        elif name in ("regfree",):
            pass
        elif name in ("__stack_chk_fail",):
            pass
        elif name in ("__cxa_finalize", "__cxa_atexit", "__register_atfork"):
            uc.reg_write(arm64_const.UC_ARM64_REG_X0, 0)
        else:
            raise NativeAlgorithmError(f"unhandled import {name!r}")

    def _alloc_heap(self, size: int) -> int:
        size = _align_up(size, 8)
        ptr = HEAP_BASE + self._heap_offset
        self._heap_offset += size
        if self._heap_offset > HEAP_SIZE:
            raise NativeAlgorithmError("emulator heap exhausted")
        return ptr

    def run_point(
        self,
        output_ptr: int,
        data_ptr: int,
        config_ptr: int,
        day: int,
        hour: int,
        minute: int,
    ) -> None:
        uc = self.uc
        uc.reg_write(arm64_const.UC_ARM64_REG_X0, output_ptr)
        uc.reg_write(arm64_const.UC_ARM64_REG_X1, data_ptr)
        uc.reg_write(arm64_const.UC_ARM64_REG_X2, config_ptr)
        uc.reg_write(arm64_const.UC_ARM64_REG_X3, day)
        uc.reg_write(arm64_const.UC_ARM64_REG_X4, hour)
        uc.reg_write(arm64_const.UC_ARM64_REG_X5, minute)
        uc.reg_write(arm64_const.UC_ARM64_REG_LR, self._sentinel)
        try:
            uc.emu_start(BASE + ADDR_CGM_ALGORITHM, self._sentinel, timeout=0,
                         count=1_000_000)
        except UcError as exc:
            pc = uc.reg_read(arm64_const.UC_ARM64_REG_PC)
            raise NativeAlgorithmError(
                f"emulation failed: {exc} at pc={hex(pc)}"
            ) from exc


def compute_official_glucose(
    readings: Sequence[SensorReading], lib_path: Path | None = None
) -> Dict[int, int]:
    """对完整会话逐点执行官方算法,返回 {glucose_id: GLU_MG (mg/dL 整数)}。

    调用方保证 readings 按 glucose_id 连续(缺洞先填补),否则算法内部
    状态会基于错误的间隔累积。每点的 day/hour/minute 由 id×3min 推导,
    与官方 JNI 层用 Calendar 从 timeMillis 拆出的语义一致。
    """
    path = lib_path or Path(
        os.environ.get("OPENANYTIME_NATIVE_LIB", str(DEFAULT_LIB_PATH))
    )
    emu = _Emulator(path)
    uc = emu.uc
    output_ptr = BASE + ADDR_OUTPUT_STRUCT
    config_ptr = HEAP_BASE + 0x2000
    data_ptr = HEAP_BASE + 0x3000
    uc.mem_write(
        config_ptr,
        struct.pack("<III f", WARMUP_POINTS, LIFE_POINTS, ALGORITHM_ID, R_DEFAULT),
    )

    results: Dict[int, int] = {}
    for reading in readings:
        iw_ptr, ib_ptr, t_ptr = data_ptr + 0x100, data_ptr + 0x108, data_ptr + 0x110
        uc.mem_write(iw_ptr, struct.pack("<f", reading.iw))
        uc.mem_write(ib_ptr, struct.pack("<f", reading.ib))
        uc.mem_write(t_ptr, struct.pack("<f", reading.temperature_c))
        # data 结构布局与 JNI 层从 DataInput 组包一致(0x68 字节):
        # id, Iw*/len, Ib*/len, T*/len, events*, BGMG*, k, reserved
        uc.mem_write(
            data_ptr,
            struct.pack("<I", reading.glucose_id)
            + struct.pack("<I", 0)
            + struct.pack("<Q", iw_ptr)
            + struct.pack("<I", 1)
            + struct.pack("<I", 0)
            + struct.pack("<Q", ib_ptr)
            + struct.pack("<I", 1)
            + struct.pack("<I", 0)
            + struct.pack("<Q", t_ptr)
            + struct.pack("<I", 1)
            + struct.pack("<I", 0)
            + struct.pack("<Q", 0)
            + struct.pack("<I", 0)
            + struct.pack("<I", 0)
            + struct.pack("<Q", 0)
            + struct.pack("<I", 0)
            + struct.pack("<f", K_DEFAULT)
            + struct.pack("<I", 0)
            + struct.pack("<I", 0)
            + struct.pack("<Q", 0),
        )
        uc.mem_write(output_ptr, b"\x00" * 0x30)
        minutes = reading.glucose_id * 3
        emu.run_point(
            output_ptr, data_ptr, config_ptr,
            minutes // 1440, (minutes % 1440) // 60, minutes % 60,
        )
        raw = bytes(uc.mem_read(output_ptr, 0x30))
        results[reading.glucose_id] = struct.unpack("<h", raw[4:6])[0]
    return results
