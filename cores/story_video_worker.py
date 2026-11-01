"""Dedicated local-only worker; started through story_video_offline bootstrap."""
import os
import sys
import traceback


def main():
    # Apply inside the fresh child as well, including when an older parent
    # server still has the pre-upgrade build_env function imported.
    os.environ['V41_QWEN_PORT'] = '18080'
    os.environ['V41_QWEN_URL'] = 'http://127.0.0.1:18080'
    from cores import runtime_core as runtime
    from cores.story_video_core import run_job, ControlStop
    if len(sys.argv) != 2:
        raise SystemExit('usage: story_video_worker task_id')
    tid = sys.argv[1]
    try:
        run_job(tid)
    except ControlStop:
        return 0
    except Exception as error:
        # Error reporting must not overwrite a concurrent parent control or
        # newer progress snapshot with a task read before the failure.
        runtime.update(tid, error=str(error)[:1500])
        traceback.print_exc()
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
