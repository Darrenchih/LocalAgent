from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

from agent_job import AgentJob
from send_status import send_status

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CTRL_BREAK_EVENT = 1
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
STILL_ACTIVE = 259

kernel32.CreateProcessW.argtypes = (
    wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID,
    wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR,
    wintypes.LPVOID, wintypes.LPVOID,
)
kernel32.CreateProcessW.restype = wintypes.BOOL
kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
kernel32.ResumeThread.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GenerateConsoleCtrlEvent.argtypes = (wintypes.DWORD, wintypes.DWORD)
kernel32.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
kernel32.GetExitCodeProcess.restype = wintypes.BOOL


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def notify(message: str) -> None:
    try:
        send_status(message)
    except Exception:
        print("Status notification unavailable.")


def create_suspended(
    command: list[str], working_directory: str | None = None
) -> PROCESS_INFORMATION:
    startup_info = STARTUPINFOW()
    startup_info.cb = ctypes.sizeof(startup_info)
    process_info = PROCESS_INFORMATION()
    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))

    created = kernel32.CreateProcessW(
        None,
        command_line,
        None,
        None,
        False,
        CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP,
        None,
        working_directory,
        ctypes.byref(startup_info),
        ctypes.byref(process_info),
    )
    if not created:
        raise ctypes.WinError(ctypes.get_last_error())

    return process_info


def close_process_handles(process_info: PROCESS_INFORMATION) -> None:
    close_error: OSError | None = None
    if process_info.hThread:
        handle = process_info.hThread
        process_info.hThread = None
        if not kernel32.CloseHandle(handle):
            close_error = ctypes.WinError(ctypes.get_last_error())
    if process_info.hProcess:
        handle = process_info.hProcess
        process_info.hProcess = None
        if not kernel32.CloseHandle(handle) and close_error is None:
            close_error = ctypes.WinError(ctypes.get_last_error())
    if close_error is not None:
        raise close_error


def _terminate_and_reap(process_info: PROCESS_INFORMATION) -> None:
    if process_info.hProcess:
        if not kernel32.TerminateProcess(process_info.hProcess, 1):
            error = ctypes.get_last_error()
            # The process may have exited between the caller's failure and cleanup.
            if kernel32.WaitForSingleObject(process_info.hProcess, 0) != WAIT_OBJECT_0:
                raise ctypes.WinError(error)
        result = kernel32.WaitForSingleObject(process_info.hProcess, 5000)
        if result == WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        if result == WAIT_TIMEOUT:
            raise TimeoutError("Timed out waiting for the failed child process to exit.")
        if result != WAIT_OBJECT_0:
            raise RuntimeError(f"Unexpected process wait result: {result}.")


def launch_in_job(
    command: list[str], working_directory: str | None = None
) -> tuple[AgentJob, PROCESS_INFORMATION]:
    process_info = create_suspended(command, working_directory)
    job: AgentJob | None = None
    try:
        # Keep the child suspended until the kill-on-close Job is ready and assigned.
        job = AgentJob()
        job.assign(process_info.hProcess)

        if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())

        return job, process_info
    except BaseException:
        try:
            _terminate_and_reap(process_info)
        finally:
            try:
                if job is not None:
                    job.close()
            finally:
                close_process_handles(process_info)
        raise


def process_exit_code(process_info: PROCESS_INFORMATION) -> int:
    exit_code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(process_info.hProcess, ctypes.byref(exit_code)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(exit_code.value)


def graceful_then_force_stop(
    job: AgentJob,
    process_info: PROCESS_INFORMATION,
    timeout_seconds: float = 2.0,
) -> bool:
    # A missing console/event is not fatal; the Job Object remains the fallback.
    kernel32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, process_info.dwProcessId)

    wait_result = kernel32.WaitForSingleObject(
        process_info.hProcess,
        int(timeout_seconds * 1000),
    )
    if wait_result == WAIT_OBJECT_0:
        return False
    if wait_result == WAIT_FAILED:
        raise ctypes.WinError(ctypes.get_last_error())
    if wait_result != WAIT_TIMEOUT:
        raise RuntimeError(f"Unexpected process wait result: {wait_result}.")

    job.terminate()
    wait_result = kernel32.WaitForSingleObject(process_info.hProcess, 5000)
    if wait_result == WAIT_FAILED:
        raise ctypes.WinError(ctypes.get_last_error())
    if wait_result == WAIT_TIMEOUT:
        raise TimeoutError("The Job Object did not stop its root process in time.")
    if wait_result != WAIT_OBJECT_0:
        raise RuntimeError(f"Unexpected process wait result: {wait_result}.")

    return True


def run_dummy_test() -> None:
    child_program = (
        "import signal, subprocess, sys, time; "
        "signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
        "subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGBREAK, signal.SIG_IGN); time.sleep(60)']); "
        "time.sleep(60)"
    )
    command = [sys.executable, "-c", child_program]

    job, process_info = launch_in_job(command)
    try:
        print("Dummy process started inside the dedicated Job Object.")
        notify("Local Agent status: dummy Job Object test started.")
        time.sleep(1)

        forced = graceful_then_force_stop(job, process_info)
        if not forced:
            raise RuntimeError("Dummy process exited before forced Job termination.")

        print("Dummy process tree stopped by its dedicated Job Object.")
        notify("Local Agent status: dummy Job Object test stopped successfully.")
    finally:
        job.close()
        close_process_handles(process_info)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dummy-test", action="store_true")
    arguments = parser.parse_args()

    if not arguments.dummy_test:
        print("No Agent command is configured. Only --dummy-test is allowed.")
        return 2

    run_dummy_test()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Launcher failed: {error}")
        notify("Local Agent status: launcher test failed.")
        raise SystemExit(1)
