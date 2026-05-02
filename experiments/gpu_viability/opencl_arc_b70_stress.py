from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / ".tmp" / "gpu-viability-opencl"

CL_SUCCESS = 0
CL_DEVICE_TYPE_GPU = 1 << 2
CL_TRUE = 1
CL_MEM_READ_WRITE = 1 << 0

CL_PLATFORM_PROFILE = 0x0900
CL_PLATFORM_VERSION = 0x0901
CL_PLATFORM_NAME = 0x0902
CL_PLATFORM_VENDOR = 0x0903
CL_DEVICE_TYPE = 0x1000
CL_DEVICE_VENDOR_ID = 0x1001
CL_DEVICE_MAX_COMPUTE_UNITS = 0x1002
CL_DEVICE_MAX_WORK_GROUP_SIZE = 0x1004
CL_DEVICE_MAX_CLOCK_FREQUENCY = 0x100C
CL_DEVICE_GLOBAL_MEM_SIZE = 0x101F
CL_DEVICE_NAME = 0x102B
CL_DEVICE_VENDOR = 0x102C
CL_DRIVER_VERSION = 0x102D
CL_DEVICE_VERSION = 0x102F
CL_DEVICE_OPENCL_C_VERSION = 0x103D
CL_PROGRAM_BUILD_LOG = 0x1183


KERNEL_SOURCE = r"""
__kernel void fill_buffers(
    __global float* a,
    __global float* b,
    __global float* c,
    const int n
) {
    const int i = (int)get_global_id(0);
    if (i >= n) {
        return;
    }
    const float x = (float)(i & 1023) * 0.0009765625f;
    a[i] = x;
    b[i] = 1.0f + x;
    c[i] = 0.5f + x;
}

__kernel void memory_triad(
    __global float* a,
    __global float* b,
    __global float* c,
    const float alpha,
    const int n,
    const int inner_iters
) {
    const int i = (int)get_global_id(0);
    if (i >= n) {
        return;
    }
    for (int k = 0; k < inner_iters; ++k) {
        const float x = b[i];
        const float y = c[i];
        const float z = mad(alpha, y, x);
        a[i] = z;
        b[i] = mad(0.99991f, z, 0.00013f * y);
        c[i] = mad(0.33331f, z, 0.66669f * x);
    }
}

__kernel void compute_mix(
    __global float* a,
    __global float* b,
    __global float* c,
    const int n,
    const int inner_iters
) {
    const int i = (int)get_global_id(0);
    if (i >= n) {
        return;
    }
    float x = a[i];
    float y = b[i];
    float z = c[i];
    for (int k = 0; k < inner_iters; ++k) {
        x = mad(x, 1.000113f, y * 0.000031f) + 0.000017f;
        y = mad(y, 0.999887f, z * 0.000047f) + 0.000019f;
        z = mad(z, 1.000071f, x * 0.000029f) + 0.000023f;
        x = native_sin(x) + native_cos(y) + z * 0.001f;
        y = native_cos(z) + x * 0.002f;
        z = native_sin(y) + z * 0.998f;
    }
    a[i] = x;
    b[i] = y;
    c[i] = z;
}

__kernel void fma_peak(
    __global float* a,
    __global float* b,
    __global float* c,
    const int n,
    const int inner_iters
) {
    const int i = (int)get_global_id(0);
    if (i >= n) {
        return;
    }
    const float seed = a[i] + (float)(i & 255) * 0.000003814697265625f;
    float x0 = seed + 0.01f;
    float x1 = seed + 0.02f;
    float x2 = seed + 0.03f;
    float x3 = seed + 0.04f;
    float x4 = seed + 0.05f;
    float x5 = seed + 0.06f;
    float x6 = seed + 0.07f;
    float x7 = seed + 0.08f;
    float x8 = seed + 0.09f;
    float x9 = seed + 0.10f;
    float xa = seed + 0.11f;
    float xb = seed + 0.12f;
    float xc = seed + 0.13f;
    float xd = seed + 0.14f;
    float xe = seed + 0.15f;
    float xf = seed + 0.16f;

    for (int k = 0; k < inner_iters; ++k) {
        x0 = fma(x0, 1.000001f, x8);
        x1 = fma(x1, 0.999999f, x9);
        x2 = fma(x2, 1.000003f, xa);
        x3 = fma(x3, 0.999997f, xb);
        x4 = fma(x4, 1.000005f, xc);
        x5 = fma(x5, 0.999995f, xd);
        x6 = fma(x6, 1.000007f, xe);
        x7 = fma(x7, 0.999993f, xf);
        x8 = fma(x8, 1.000011f, x0);
        x9 = fma(x9, 0.999989f, x1);
        xa = fma(xa, 1.000013f, x2);
        xb = fma(xb, 0.999987f, x3);
        xc = fma(xc, 1.000017f, x4);
        xd = fma(xd, 0.999983f, x5);
        xe = fma(xe, 1.000019f, x6);
        xf = fma(xf, 0.999981f, x7);

        x0 = fma(x0, 0.500001f, x1);
        x1 = fma(x1, 0.500003f, x2);
        x2 = fma(x2, 0.500005f, x3);
        x3 = fma(x3, 0.500007f, x4);
        x4 = fma(x4, 0.500009f, x5);
        x5 = fma(x5, 0.500011f, x6);
        x6 = fma(x6, 0.500013f, x7);
        x7 = fma(x7, 0.500015f, x8);
        x8 = fma(x8, 0.500017f, x9);
        x9 = fma(x9, 0.500019f, xa);
        xa = fma(xa, 0.500021f, xb);
        xb = fma(xb, 0.500023f, xc);
        xc = fma(xc, 0.500025f, xd);
        xd = fma(xd, 0.500027f, xe);
        xe = fma(xe, 0.500029f, xf);
        xf = fma(xf, 0.500031f, x0);
    }
    const float folded =
        (x0 + x1 + x2 + x3) +
        (x4 + x5 + x6 + x7) +
        (x8 + x9 + xa + xb) +
        (xc + xd + xe + xf);
    a[i] = folded;
    b[i] = folded * 0.5f + x0;
    c[i] = folded * 0.25f + xf;
}
"""


class OpenCLError(RuntimeError):
    pass


class OpenCL:
    def __init__(self) -> None:
        if os.name == "nt":
            self.lib = ctypes.WinDLL("OpenCL.dll")
        else:
            self.lib = ctypes.CDLL("libOpenCL.so")
        self._bind()

    def _bind(self) -> None:
        c_void_p = ctypes.c_void_p
        c_uint = ctypes.c_uint
        c_int = ctypes.c_int
        c_size_t = ctypes.c_size_t
        c_ulong = ctypes.c_ulong

        self.lib.clGetPlatformIDs.argtypes = [c_uint, ctypes.POINTER(c_void_p), ctypes.POINTER(c_uint)]
        self.lib.clGetPlatformIDs.restype = c_int
        self.lib.clGetPlatformInfo.argtypes = [c_void_p, c_uint, c_size_t, c_void_p, ctypes.POINTER(c_size_t)]
        self.lib.clGetPlatformInfo.restype = c_int
        self.lib.clGetDeviceIDs.argtypes = [c_void_p, c_ulong, c_uint, ctypes.POINTER(c_void_p), ctypes.POINTER(c_uint)]
        self.lib.clGetDeviceIDs.restype = c_int
        self.lib.clGetDeviceInfo.argtypes = [c_void_p, c_uint, c_size_t, c_void_p, ctypes.POINTER(c_size_t)]
        self.lib.clGetDeviceInfo.restype = c_int
        self.lib.clCreateContext.argtypes = [c_void_p, c_uint, ctypes.POINTER(c_void_p), c_void_p, c_void_p, ctypes.POINTER(c_int)]
        self.lib.clCreateContext.restype = c_void_p
        self.lib.clCreateCommandQueue.argtypes = [c_void_p, c_void_p, c_ulong, ctypes.POINTER(c_int)]
        self.lib.clCreateCommandQueue.restype = c_void_p
        self.lib.clCreateProgramWithSource.argtypes = [c_void_p, c_uint, ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(c_size_t), ctypes.POINTER(c_int)]
        self.lib.clCreateProgramWithSource.restype = c_void_p
        self.lib.clBuildProgram.argtypes = [c_void_p, c_uint, ctypes.POINTER(c_void_p), ctypes.c_char_p, c_void_p, c_void_p]
        self.lib.clBuildProgram.restype = c_int
        self.lib.clGetProgramBuildInfo.argtypes = [c_void_p, c_void_p, c_uint, c_size_t, c_void_p, ctypes.POINTER(c_size_t)]
        self.lib.clGetProgramBuildInfo.restype = c_int
        self.lib.clCreateKernel.argtypes = [c_void_p, ctypes.c_char_p, ctypes.POINTER(c_int)]
        self.lib.clCreateKernel.restype = c_void_p
        self.lib.clCreateBuffer.argtypes = [c_void_p, c_ulong, c_size_t, c_void_p, ctypes.POINTER(c_int)]
        self.lib.clCreateBuffer.restype = c_void_p
        self.lib.clSetKernelArg.argtypes = [c_void_p, c_uint, c_size_t, c_void_p]
        self.lib.clSetKernelArg.restype = c_int
        self.lib.clEnqueueNDRangeKernel.argtypes = [
            c_void_p,
            c_void_p,
            c_uint,
            ctypes.POINTER(c_size_t),
            ctypes.POINTER(c_size_t),
            ctypes.POINTER(c_size_t),
            c_uint,
            c_void_p,
            c_void_p,
        ]
        self.lib.clEnqueueNDRangeKernel.restype = c_int
        self.lib.clEnqueueReadBuffer.argtypes = [c_void_p, c_void_p, c_uint, c_size_t, c_size_t, c_void_p, c_uint, c_void_p, c_void_p]
        self.lib.clEnqueueReadBuffer.restype = c_int
        self.lib.clFinish.argtypes = [c_void_p]
        self.lib.clFinish.restype = c_int
        for name in (
            "clReleaseMemObject",
            "clReleaseKernel",
            "clReleaseProgram",
            "clReleaseCommandQueue",
            "clReleaseContext",
        ):
            getattr(self.lib, name).argtypes = [c_void_p]
            getattr(self.lib, name).restype = c_int


def check(status: int, action: str) -> None:
    if status != CL_SUCCESS:
        raise OpenCLError(f"{action} failed with OpenCL status {status}")


def get_info_string(cl: OpenCL, fn: Any, handle: ctypes.c_void_p, param: int) -> str:
    size = ctypes.c_size_t()
    status = fn(handle, param, 0, None, ctypes.byref(size))
    check(status, f"get info size {param}")
    buffer = ctypes.create_string_buffer(size.value)
    status = fn(handle, param, size, buffer, None)
    check(status, f"get info {param}")
    return buffer.value.decode("utf-8", errors="replace")


def get_device_uint(cl: OpenCL, device: ctypes.c_void_p, param: int) -> int:
    value = ctypes.c_uint()
    status = cl.lib.clGetDeviceInfo(device, param, ctypes.sizeof(value), ctypes.byref(value), None)
    check(status, f"get device uint {param}")
    return int(value.value)


def get_device_ulong(cl: OpenCL, device: ctypes.c_void_p, param: int) -> int:
    value = ctypes.c_uint64()
    status = cl.lib.clGetDeviceInfo(device, param, ctypes.sizeof(value), ctypes.byref(value), None)
    check(status, f"get device ulong {param}")
    return int(value.value)


def get_device_size_t(cl: OpenCL, device: ctypes.c_void_p, param: int) -> int:
    value = ctypes.c_size_t()
    status = cl.lib.clGetDeviceInfo(device, param, ctypes.sizeof(value), ctypes.byref(value), None)
    check(status, f"get device size_t {param}")
    return int(value.value)


def enumerate_devices(cl: OpenCL) -> list[dict[str, Any]]:
    platform_count = ctypes.c_uint()
    check(cl.lib.clGetPlatformIDs(0, None, ctypes.byref(platform_count)), "clGetPlatformIDs count")
    platforms = (ctypes.c_void_p * platform_count.value)()
    check(cl.lib.clGetPlatformIDs(platform_count, platforms, None), "clGetPlatformIDs")
    devices: list[dict[str, Any]] = []
    for platform_index, platform in enumerate(platforms):
        platform_name = get_info_string(cl, cl.lib.clGetPlatformInfo, platform, CL_PLATFORM_NAME)
        platform_vendor = get_info_string(cl, cl.lib.clGetPlatformInfo, platform, CL_PLATFORM_VENDOR)
        platform_version = get_info_string(cl, cl.lib.clGetPlatformInfo, platform, CL_PLATFORM_VERSION)
        device_count = ctypes.c_uint()
        status = cl.lib.clGetDeviceIDs(platform, CL_DEVICE_TYPE_GPU, 0, None, ctypes.byref(device_count))
        if status != CL_SUCCESS or device_count.value == 0:
            continue
        raw_devices = (ctypes.c_void_p * device_count.value)()
        check(cl.lib.clGetDeviceIDs(platform, CL_DEVICE_TYPE_GPU, device_count, raw_devices, None), "clGetDeviceIDs")
        for device_index, device in enumerate(raw_devices):
            devices.append(
                {
                    "platform_index": platform_index,
                    "device_index": device_index,
                    "platform_handle": platform,
                    "device_handle": device,
                    "platform_name": platform_name,
                    "platform_vendor": platform_vendor,
                    "platform_version": platform_version,
                    "device_name": get_info_string(cl, cl.lib.clGetDeviceInfo, device, CL_DEVICE_NAME),
                    "device_vendor": get_info_string(cl, cl.lib.clGetDeviceInfo, device, CL_DEVICE_VENDOR),
                    "driver_version": get_info_string(cl, cl.lib.clGetDeviceInfo, device, CL_DRIVER_VERSION),
                    "device_version": get_info_string(cl, cl.lib.clGetDeviceInfo, device, CL_DEVICE_VERSION),
                    "opencl_c_version": get_info_string(cl, cl.lib.clGetDeviceInfo, device, CL_DEVICE_OPENCL_C_VERSION),
                    "global_mem_bytes": get_device_ulong(cl, device, CL_DEVICE_GLOBAL_MEM_SIZE),
                    "compute_units": get_device_uint(cl, device, CL_DEVICE_MAX_COMPUTE_UNITS),
                    "max_clock_mhz": get_device_uint(cl, device, CL_DEVICE_MAX_CLOCK_FREQUENCY),
                    "max_work_group_size": get_device_size_t(cl, device, CL_DEVICE_MAX_WORK_GROUP_SIZE),
                }
            )
    return devices


def select_device(devices: list[dict[str, Any]], contains: str) -> dict[str, Any]:
    lowered = contains.lower()
    for device in devices:
        haystack = f"{device['device_name']} {device['device_vendor']} {device['platform_name']}".lower()
        if lowered in haystack:
            return device
    for device in devices:
        if "intel" in f"{device['device_name']} {device['device_vendor']} {device['platform_name']}".lower():
            return device
    if devices:
        return devices[0]
    raise OpenCLError("No OpenCL GPU devices were found.")


class OpenCLRuntime:
    def __init__(self, cl: OpenCL, device: dict[str, Any], *, build_options: str = "") -> None:
        self.cl = cl
        self.device = device
        self.build_options = build_options
        self.context = None
        self.queue = None
        self.program = None
        self.kernels: dict[str, ctypes.c_void_p] = {}
        self.buffers: list[ctypes.c_void_p] = []
        self._create()

    def _create(self) -> None:
        err = ctypes.c_int()
        device_array = (ctypes.c_void_p * 1)(self.device["device_handle"])
        self.context = self.cl.lib.clCreateContext(None, 1, device_array, None, None, ctypes.byref(err))
        check(err.value, "clCreateContext")
        self.queue = self.cl.lib.clCreateCommandQueue(self.context, self.device["device_handle"], 0, ctypes.byref(err))
        check(err.value, "clCreateCommandQueue")
        source = KERNEL_SOURCE.encode("utf-8")
        sources = (ctypes.c_char_p * 1)(source)
        lengths = (ctypes.c_size_t * 1)(len(source))
        self.program = self.cl.lib.clCreateProgramWithSource(self.context, 1, sources, lengths, ctypes.byref(err))
        check(err.value, "clCreateProgramWithSource")
        device_array = (ctypes.c_void_p * 1)(self.device["device_handle"])
        status = self.cl.lib.clBuildProgram(
            self.program,
            1,
            device_array,
            self.build_options.encode("utf-8"),
            None,
            None,
        )
        if status != CL_SUCCESS:
            raise OpenCLError(self.build_log())
        for name in ("fill_buffers", "memory_triad", "compute_mix", "fma_peak"):
            kernel = self.cl.lib.clCreateKernel(self.program, name.encode("utf-8"), ctypes.byref(err))
            check(err.value, f"clCreateKernel {name}")
            self.kernels[name] = kernel

    def build_log(self) -> str:
        size = ctypes.c_size_t()
        self.cl.lib.clGetProgramBuildInfo(self.program, self.device["device_handle"], CL_PROGRAM_BUILD_LOG, 0, None, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(max(size.value, 1))
        self.cl.lib.clGetProgramBuildInfo(self.program, self.device["device_handle"], CL_PROGRAM_BUILD_LOG, size, buffer, None)
        return buffer.value.decode("utf-8", errors="replace")

    def create_buffer(self, size_bytes: int) -> ctypes.c_void_p:
        err = ctypes.c_int()
        buffer = self.cl.lib.clCreateBuffer(self.context, CL_MEM_READ_WRITE, ctypes.c_size_t(size_bytes), None, ctypes.byref(err))
        check(err.value, f"clCreateBuffer {size_bytes}")
        self.buffers.append(buffer)
        return buffer

    def set_arg_mem(self, kernel: ctypes.c_void_p, index: int, value: ctypes.c_void_p) -> None:
        mem_value = ctypes.c_void_p(value)
        check(self.cl.lib.clSetKernelArg(kernel, index, ctypes.sizeof(mem_value), ctypes.byref(mem_value)), f"clSetKernelArg mem {index}")

    def set_arg_int(self, kernel: ctypes.c_void_p, index: int, value: int) -> None:
        int_value = ctypes.c_int(int(value))
        check(self.cl.lib.clSetKernelArg(kernel, index, ctypes.sizeof(int_value), ctypes.byref(int_value)), f"clSetKernelArg int {index}")

    def set_arg_float(self, kernel: ctypes.c_void_p, index: int, value: float) -> None:
        float_value = ctypes.c_float(float(value))
        check(self.cl.lib.clSetKernelArg(kernel, index, ctypes.sizeof(float_value), ctypes.byref(float_value)), f"clSetKernelArg float {index}")

    def enqueue(self, kernel: ctypes.c_void_p, global_items: int, local_items: int) -> None:
        rounded = int(math.ceil(global_items / local_items) * local_items)
        global_size = (ctypes.c_size_t * 1)(rounded)
        local_size = (ctypes.c_size_t * 1)(local_items)
        check(
            self.cl.lib.clEnqueueNDRangeKernel(
                self.queue,
                kernel,
                1,
                None,
                global_size,
                local_size,
                0,
                None,
                None,
            ),
            "clEnqueueNDRangeKernel",
        )

    def finish(self) -> None:
        check(self.cl.lib.clFinish(self.queue), "clFinish")

    def read_floats(self, buffer: ctypes.c_void_p, count: int) -> list[float]:
        array_type = ctypes.c_float * count
        output = array_type()
        check(
            self.cl.lib.clEnqueueReadBuffer(
                self.queue,
                buffer,
                CL_TRUE,
                0,
                ctypes.sizeof(output),
                ctypes.byref(output),
                0,
                None,
                None,
            ),
            "clEnqueueReadBuffer",
        )
        return [float(item) for item in output]

    def release(self) -> None:
        for buffer in reversed(self.buffers):
            self.cl.lib.clReleaseMemObject(buffer)
        for kernel in self.kernels.values():
            self.cl.lib.clReleaseKernel(kernel)
        if self.program:
            self.cl.lib.clReleaseProgram(self.program)
        if self.queue:
            self.cl.lib.clReleaseCommandQueue(self.queue)
        if self.context:
            self.cl.lib.clReleaseContext(self.context)


def run_phase(
    runtime: OpenCLRuntime,
    *,
    total_gb: float,
    seconds: float,
    inner_iters: int,
    local_items: int,
    kernel_kind: str,
) -> dict[str, Any]:
    total_bytes = int(total_gb * (1024**3))
    elements = max(total_bytes // (3 * 4), local_items)
    buffer_bytes = elements * 4
    a = runtime.create_buffer(buffer_bytes)
    b = runtime.create_buffer(buffer_bytes)
    c = runtime.create_buffer(buffer_bytes)

    fill = runtime.kernels["fill_buffers"]
    for index, buffer in enumerate((a, b, c)):
        runtime.set_arg_mem(fill, index, buffer)
    runtime.set_arg_int(fill, 3, elements)
    runtime.enqueue(fill, elements, local_items)
    runtime.finish()

    kernel_name = kernel_kind
    kernel = runtime.kernels[kernel_name]
    runtime.set_arg_mem(kernel, 0, a)
    runtime.set_arg_mem(kernel, 1, b)
    runtime.set_arg_mem(kernel, 2, c)
    if kernel_name in {"compute_mix", "fma_peak"}:
        runtime.set_arg_int(kernel, 3, elements)
        runtime.set_arg_int(kernel, 4, inner_iters)
    else:
        runtime.set_arg_float(kernel, 3, 1.61803398875)
        runtime.set_arg_int(kernel, 4, elements)
        runtime.set_arg_int(kernel, 5, inner_iters)

    launches = 0
    phase_start = time.perf_counter()
    last_print = phase_start
    while True:
        runtime.enqueue(kernel, elements, local_items)
        runtime.finish()
        launches += 1
        now = time.perf_counter()
        if now - last_print >= 5.0:
            print(f"    {kernel_name} {total_gb:.2f} GB: {launches} launches, {now - phase_start:.1f}s")
            last_print = now
        if now - phase_start >= seconds:
            break

    elapsed = time.perf_counter() - phase_start
    sample = runtime.read_floats(a, 8)
    if kernel_name == "compute_mix":
        flops = elements * launches * inner_iters * 28
        estimated_gb_s = None
        estimated_tflops = flops / max(elapsed, 1.0e-12) / 1.0e12
    elif kernel_name == "fma_peak":
        flops = elements * launches * inner_iters * 32 * 2
        estimated_gb_s = None
        estimated_tflops = flops / max(elapsed, 1.0e-12) / 1.0e12
    else:
        bytes_touched = elements * launches * inner_iters * 6 * 4
        estimated_gb_s = bytes_touched / max(elapsed, 1.0e-12) / 1.0e9
        estimated_tflops = None
    return {
        "kernel": kernel_name,
        "total_buffer_gb": round(total_gb, 3),
        "elements": int(elements),
        "inner_iters": int(inner_iters),
        "launches": int(launches),
        "elapsed_s": round(elapsed, 6),
        "estimated_gb_s": round(estimated_gb_s, 3) if estimated_gb_s is not None else None,
        "estimated_tflops": round(estimated_tflops, 3) if estimated_tflops is not None else None,
        "sample": [round(item, 6) for item in sample],
    }


def host_report() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "executable": sys.executable,
        "repo_root": str(REPO_ROOT),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def parse_ramp(raw: str) -> list[float]:
    return [float(item.strip()) for item in raw.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Direct OpenCL stress test for Intel Arc Pro B70 GPU viability.")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--device-contains", default="Arc")
    parser.add_argument("--ramp-gb", default="1,2,4,8", help="Comma-separated total buffer GB values across 3 float buffers.")
    parser.add_argument("--seconds-per-phase", type=float, default=20.0)
    parser.add_argument("--inner-iters", type=int, default=4)
    parser.add_argument("--local-items", type=int, default=256)
    parser.add_argument("--compute-mix", action="store_true", help="Also run a math-heavy kernel phase.")
    parser.add_argument("--fma-peak", action="store_true", help="Also run a pure FP32 FMA peak-throughput phase.")
    parser.add_argument("--fma-gb", type=float, default=1.0, help="Total buffer GB for the FMA peak phase.")
    parser.add_argument("--fma-inner-iters", type=int, default=256, help="Inner iterations for the FMA peak phase.")
    parser.add_argument("--build-options", default="-cl-fast-relaxed-math -cl-mad-enable", help="OpenCL program build options.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args(argv)

    cl = OpenCL()
    devices = enumerate_devices(cl)
    public_devices = [
        {key: value for key, value in device.items() if key not in {"platform_handle", "device_handle"}}
        for device in devices
    ]
    if args.list_devices:
        print(json.dumps({"host": host_report(), "devices": public_devices}, indent=2))
        return 0

    selected = select_device(devices, args.device_contains)
    public_selected = {key: value for key, value in selected.items() if key not in {"platform_handle", "device_handle"}}
    print(f"selected OpenCL GPU: {selected['device_name']} ({selected['device_vendor']})")
    print(f"driver: {selected['driver_version']} | global memory: {selected['global_mem_bytes'] / (1024**3):.2f} GiB")

    runtime = OpenCLRuntime(cl, selected, build_options=args.build_options)
    results = []
    try:
        for total_gb in parse_ramp(args.ramp_gb):
            max_gb = selected["global_mem_bytes"] / (1024**3) * 0.82
            if total_gb > max_gb:
                print(f"skipping {total_gb:.2f} GB; above conservative 82% device-memory limit {max_gb:.2f} GB")
                continue
            print(f"  memory phase: {total_gb:.2f} GB buffers for {args.seconds_per_phase:.1f}s")
            results.append(
                run_phase(
                    runtime,
                    total_gb=total_gb,
                    seconds=args.seconds_per_phase,
                    inner_iters=args.inner_iters,
                    local_items=args.local_items,
                    kernel_kind="memory_triad",
                )
            )
        if args.compute_mix:
            compute_gb = min(parse_ramp(args.ramp_gb)[-1], 4.0)
            print(f"  compute phase: {compute_gb:.2f} GB buffers for {args.seconds_per_phase:.1f}s")
            results.append(
                run_phase(
                    runtime,
                    total_gb=compute_gb,
                    seconds=args.seconds_per_phase,
                    inner_iters=max(args.inner_iters * 16, 64),
                    local_items=args.local_items,
                    kernel_kind="compute_mix",
                )
            )
        if args.fma_peak:
            fma_gb = min(args.fma_gb, selected["global_mem_bytes"] / (1024**3) * 0.5)
            print(f"  fma peak phase: {fma_gb:.2f} GB buffers for {args.seconds_per_phase:.1f}s")
            results.append(
                run_phase(
                    runtime,
                    total_gb=fma_gb,
                    seconds=args.seconds_per_phase,
                    inner_iters=args.fma_inner_iters,
                    local_items=args.local_items,
                    kernel_kind="fma_peak",
                )
            )
    finally:
        runtime.release()

    payload = {
        "host": host_report(),
        "selected_device": public_selected,
        "all_devices": public_devices,
        "parameters": {
            "ramp_gb": parse_ramp(args.ramp_gb),
            "seconds_per_phase": args.seconds_per_phase,
            "inner_iters": args.inner_iters,
            "local_items": args.local_items,
            "compute_mix": args.compute_mix,
            "fma_peak": args.fma_peak,
            "fma_gb": args.fma_gb,
            "fma_inner_iters": args.fma_inner_iters,
            "build_options": args.build_options,
        },
        "results": results,
    }
    stamp = time.strftime("%Y%m%d-%H%M%S")
    output_path = Path(args.output_root).resolve() / f"opencl_arc_stress_{stamp}.json"
    write_json(output_path, payload)
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
