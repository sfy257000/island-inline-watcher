# 島語 inline 空位監看 v1.00

Windows 桌面程式，可依分店、人數、日期和時段監看島語空位。支援 Email 通知，也能在你開啟自動訂位後，嘗試送出第一筆符合條件的訂位。簡訊驗證可在 Chrome 手動輸入，也可透過 Gmail 回信交給程式送出。

## 一鍵啟動

Windows 需已安裝 **Google Chrome**。在 PowerShell 執行：

```powershell
git clone https://github.com/sfy257000/island-inline-watcher.git
cd island-inline-watcher
.\start.bat
```

也可以直接雙擊專案資料夾裡的 **`start.bat`**。若從 GitHub 的 **Code → Download ZIP** 下載，請先解壓縮整個專案。

首次啟動需要網路。啟動檔會從 Astral 下載固定版本的 uv 到專案內的 `.tools`，再透過 uv 準備 Python 3.12，建立 `.venv`，依 `uv.lock` 安裝 Playwright，最後打開桌面視窗。以後再次啟動會重複使用這個環境。

如果已安裝 uv，也可以在專案資料夾執行：

```powershell
uv run --locked --managed-python --python 3.12 python island_inline_watcher.py
```

## 使用方式

- 選分店、大人及小孩人數，再選單日、日期範圍或「不限日期」。可加上週末、假日或用餐時段條件。
- 按「查詢一次」只查時段，即使已勾選自動訂位也一樣。按「開始監看」可持續查詢。輪間隔可填 4 至 86400 秒。
- 要收信，從「Email 設定」設定 SMTP 和收件地址，並勾選「Email 通知」。收件欄可填多人，每行一個地址，也支援逗號或分號分隔；重複地址會自動合併。程式會分別寄信給每位收件人，並各自記錄已通知時段，失敗的收件人會在後續查詢仍有相同空位時補寄。
- 要啟動自動訂位，填「訂位資料」，勾選「自動訂位」後再開始監看。「不限桌型」會先嘗試一般座位，再嘗試板前吧台。

監看時需保持程式和 Chrome 開啟。程式每輪會載入一次分店的訂位頁，透過頁面上的人數與日曆選項查詢。查同一輪的其他日期時會沿用該頁。

查詢與診斷使用正常啟動的 Google Chrome，透過本機限定的頁面控制接口操作，不開啟 JavaScript 調試事件訂閱，不覆寫螢幕、語言、瀏覽器屬性或網站驗證程式。兩者共用 `.browser-native-profile/` 的專用資料夾，關閉後會保留 Cookie 與網站儲存；這個資料夾不提交 Git。程式不使用平常 Chrome 的個人設定檔。同一時間只能開啟一個使用此資料夾的查詢或診斷。遇到網站驗證仍會停止，保留畫面供手動處理。

網頁操作之間至少間隔 2 秒；切換人數、日期或桌型後，會等 inline 的相關請求完成、時段內容穩定至少 0.8 秒後才繼續。若頁面已顯示正確日期及人數，直接讀取結果，避免重複點選日曆。這些等待用來減少連續操作及讀到更新前的結果，不能保證 inline 不要求人機驗證。

「不限日期」會從網站目前選定的日期開始，讀取日曆已顯示的可訂日期，並依網站公布的其他可訂日期數停止掃描。若網站未提供總數，僅查目前日曆提供的日期並提示掃描可能不完整，不會盲目翻完整年。持續監看遇到載入／讀取逾時或暫時無法辨識頁面時，會保留已查結果與寄信紀錄，等輪間隔後自動重新查詢。按「停止」可取消等待中的重試。

如果 inline 顯示瀏覽器人機驗證，程式會停止這次自動操作，顯示停在哪一步並保留原本的 Chrome 視窗。先前讀到的可訂時段仍會留在結果表。請依網頁提示手動完成驗證，可直接在同一頁查詢或訂位。關閉驗證視窗後，可以再次開始監看。[inline 人機驗證說明](https://intercom.help/inlinetocustomer/en/articles/16079045-what-should-i-do-if-i-can-t-pass-human-verification)

## 下次開啟自動帶入設定

- 「訂位資料」按 **儲存**，下次會帶入姓名、手機、聯絡 Email 和桌型。
- 「Email 設定」按 **儲存**，下次會帶入 SMTP 設定與收件人。如果也要帶入密碼，請勾選 **記住應用程式密碼** 再儲存。取消勾選後再次儲存，會移除先前記住的密碼。

這些設定儲存在專案根目錄的 `.env`，重建 `.venv` 不會移除設定。`.env` 已列入 Git 忽略清單，其中的聯絡資料和勾選記住的密碼是明文，請保留在本機。自動訂位仍需在開始監看時由你確認。

## 用 Gmail 回信完成簡訊驗證

1. 在「Email 設定」填入自己的 Gmail、Google 應用程式密碼，以及 SMTP 主機 `smtp.gmail.com`、連接埠 `465`、加密 `SSL/TLS`。
2. 勾選「允許用 Gmail 回信完成簡訊驗證」，按「寄送測試信」可同時檢查 SMTP 和 Gmail IMAP 連線，再按「儲存」。收信使用 `imap.gmail.com:993`，共用同一組 Gmail 帳號和應用程式密碼。[Google 應用程式密碼說明](https://support.google.com/accounts/answer/185833?hl=zh-Hant)
3. 勾選「Email 通知」和「自動訂位」，開始監看。當 inline 顯示簡訊驗證欄位，程式會把驗證通知寄給上方的**寄件 Gmail 帳號**。其他設定的收件人會收到一般通知。若先遇到訂金或條款畫面，請在原本的 Chrome 視窗繼續操作。出現簡訊驗證時，程式才會寄驗證信。
4. 用同一 Gmail 帳號回覆原信，**第一行只填簡訊上的 4 位數字**。在 inline 的驗證期限內回信，並保持程式與原本的 Chrome 頁面開啟。程式最多等 3 分鐘，收到符合條件的回信後會在該訂位頁送出一次驗證碼。

回信驗證預設關閉。程式以唯讀模式尋找該封通知的回信，驗證碼會留在 Gmail 回信內。請以 inline 頁面或正式通知確認訂位結果。回信逾時、收信失敗、驗證不成功，或遇到訂金及條款畫面時，請在 Chrome 繼續處理。

## 診斷與測試

每次查詢的步驟、實際操作間隔及中斷原因會寫入專案的 `logs/query-*.jsonl`。紀錄不包含姓名、手機、Email、密碼、Cookie、驗證碼或網頁內容；`logs/` 不會提交至 Git。若再次遇到驗證，可用紀錄確認發生在哪個步驟，避免反覆重試。

單日診斷不讀取已儲存的聯絡資料、不寄信、不送出訂位。`--summary` 只會選桌型與時段、核對摘要，停在開啟聯絡表單前。日期請換成要查詢的日期：

```powershell
uv run --locked --managed-python --python 3.12 python diagnose_inline.py --date 2026-11-19
uv run --locked --managed-python --python 3.12 python diagnose_inline.py --date 2026-11-19 --summary
```

加上 `--keep-open` 可保留 Chrome；遇到人機驗證時也會保留視窗，關閉該視窗後診斷才結束。

本機回歸測試使用 Chrome 與攔截的模擬頁，不會連線正式訂位服務或寄信：

```powershell
uv run --locked --managed-python --python 3.12 python -m unittest discover -s tests -v
```

Windows 實測範圍及限制見 [2026-11-10 實測紀錄](docs/windows-validation-2026-11-10.md)。
