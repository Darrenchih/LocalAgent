from __future__ import annotations

import ctypes
from typing import Any

from agent_launcher import (
    WAIT_FAILED,
    WAIT_OBJECT_0,
    WAIT_TIMEOUT,
    close_process_handles,
    graceful_then_force_stop,
    kernel32,
    launch_in_job,
    notify,
    process_exit_code,
)


def _reap_root_process(process_info: Any) -> None:
    """Ensure a closed Job's root process has exited before releasing its handle."""
    result = kernel32.WaitForSingleObject(process_info.hProcess, 5000)
    if result == WAIT_FAILED:
        raise ctypes.WinError(ctypes.get_last_error())
    if result == WAIT_TIMEOUT:
        raise TimeoutError("Timed out waiting for the Agent process to exit.")
    if result != WAIT_OBJECT_0:
        raise RuntimeError(f"Unexpected process wait result: {result}.")


def run_configured_agent(config: dict[str, Any]) -> int:
    job, process_info = launch_in_job(
        config["command"], config["working_directory"]
    )
    try:
        print("Configured Agent started inside the dedicated Job Object.")
        notify("Local Agent status: configured Agent started.")

        while True:
            wait_result = kernel32.WaitForSingleObject(process_info.hProcess, 1000)

            if wait_result == WAIT_OBJECT_0:
                exit_code = process_exit_code(process_info)
                print(f"Configured Agent exited with code {exit_code}.")
                notify("Local Agent status: configured Agent exited.")
                return exit_code

            if wait_result == WAIT_TIMEOUT:
                continue
            if wait_result == WAIT_FAILED:
                raise ctypes.WinError(ctypes.get_last_error())
            raise RuntimeError(f"Unexpected process wait result: {wait_result}.")

    except KeyboardInterrupt:
        print("Shutdown requested. Stopping only the dedicated Agent Job.")
        notify("Local Agent status: configured Agent shutdown requested.")

        forced = graceful_then_force_stop(
            job,
            process_info,
            config["graceful_shutdown_seconds"],
        )
        outcome = "forced Job termination" if forced else "graceful shutdown"
        print(f"Configured Agent stopped by {outcome}.")
        notify("Local Agent status: configured Agent stopped.")
        return process_exit_code(process_info)
    finally:
        # KILL_ON_JOB_CLOSE also removes descendants if the root exits first.
        try:
            job.close()
        finally:
            try:
                _reap_root_process(process_info)
            finally:
                close_process_handles(process_info)
