"""Run a single public availability check, optionally stopping at the booking summary."""
import argparse
import threading
from contextlib import closing
from datetime import date

from island_inline_watcher import (
    ANY_TABLE, BRANCHES, TABLE_OPTIONS, BookingProfile, BrowserFlow, Query, QueryTrace,
    check_day, launch_query_browser, start_booking, wait_for_chrome,
)


def main():
    parser = argparse.ArgumentParser(description="inline 單日診斷；不填個資、不寄信、不送出訂位。")
    parser.add_argument("--date", required=True, type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--branch", choices=BRANCHES, default="桃園台茂店")
    parser.add_argument("--adults", type=int, choices=range(1, 8), default=2)
    parser.add_argument("--kids", type=int, choices=range(6), default=0)
    parser.add_argument("--summary", action="store_true", help="核對訂位摘要後停止，不開聯絡表單")
    parser.add_argument("--table", choices=TABLE_OPTIONS, default=ANY_TABLE)
    parser.add_argument("--keep-open", action="store_true", help="查完保留 Chrome，關閉視窗後結束")
    args = parser.parse_args()
    if args.adults + args.kids > 7:
        parser.error("大人與小孩合計最多 7 位")
    cancel, trace = threading.Event(), QueryTrace()
    query = Query(args.branch, args.date, args.date, args.adults, args.kids, 120)
    print(f"步驟紀錄：{trace.path}", flush=True)
    try:
        with closing(launch_query_browser()) as browser:
            page = browser.pages[0] if browser.pages else browser.new_page()
            flow = BrowserFlow(page, cancel, trace)
            result = check_day(page, query, args.date, cancel, refresh=True, flow=flow)
            print(result, flush=True)
            trace.record("result", "讀取時段", slot_count=len(result.slots), failed=result.failed,
                         needs_verification=result.needs_verification)
            blocked = result.needs_verification
            if args.summary and result.slots and not result.stop:
                profile = BookingProfile("", "", "", "", args.table)
                handoff = start_booking(page, query, result, profile, cancel, flow=flow, preview=True)
                print(handoff or "無法確認符合條件的訂位摘要。", flush=True)
                blocked = bool(handoff and handoff.needs_verification)
            if args.keep_open or blocked:
                print("請在 Chrome 查看；關閉該視窗後結束診斷。", flush=True)
                wait_for_chrome(page, cancel)
    except KeyboardInterrupt:
        cancel.set()
    finally:
        trace.record("finished", "診斷結束")
        trace.close()


if __name__ == "__main__":
    main()
