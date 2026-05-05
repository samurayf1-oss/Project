import subprocess
import sys
import time

def run_script(script_name):
    print()
    print("=" * 50)
    print("RUNNING:", script_name)
    print("=" * 50)

    result = subprocess.run(
        [sys.executable, script_name],
        text=True
    )

    if result.returncode != 0:
        print("ERROR:", script_name, "failed")
        return False

    return True


def main():
    interval_minutes = 1
    interval_seconds = interval_minutes * 60

    while True:
        print()
        print("=" * 50)
        print("BOT CYCLE START")
        print("=" * 50)

        live_ok = run_script("live.py")

        if not live_ok:
            print("Bot cycle stopped because live.py failed")
        else:
            paper_ok = run_script("paper_trading.py")

            if not paper_ok:
                print("Bot cycle stopped because paper_trading.py failed")

        print()
        print("=" * 50)
        print("BOT CYCLE COMPLETE")
        print("Next run in", interval_minutes, "minutes")
        print("=" * 50)

        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()