# 島語 inline 空位監看 v1.04

Windows 桌面程式，可依分店、人數、日期和時段監看島語空位，支援 Email 通知與自動訂位。

> **自動訂位功能測試中（Beta）**：請留意 Chrome 畫面，並以 inline 的訂位成功頁面或正式通知確認結果。

## 安裝

需使用 Windows 10／11 x64，並先安裝 **Google Chrome**。

從 [Releases](https://github.com/sfy257000/island-inline-watcher/releases) 下載 `island-inline-watcher-v1.04-windows-x64-setup.exe`，安裝後從桌面或開始功能表開啟。不需要另外安裝 Python 或其他套件。更新時下載新版安裝檔覆蓋安裝即可。

目前安裝檔未簽章，Windows 可能顯示發行者未知或 SmartScreen 提示。請確認下載來源為本專案的 Releases；`SHA256SUMS.txt` 可用來核對下載檔是否一致。

也可以從 GitHub 的 **Code → Download ZIP** 下載原始碼，完整解壓縮後雙擊 **`start.bat`** 啟動。首次執行需連網，會自動準備 Python 與所需套件。

## 使用方式

1. 選擇分店、人數、日期及用餐時段。
2. 按「查詢一次」查看當下空位，或按「開始監看」持續查詢。輪間隔可設為 4 至 86400 秒。
3. 如需通知，完成「Email 設定」並勾選「Email 通知」。
4. 如需自動訂位，填寫「訂位資料」，勾選「自動訂位」後開始監看。「查詢一次」不會送出訂位。

監看期間請保持程式與 Chrome 開啟。遇到人機驗證時，程式會停止自動操作並保留 Chrome，請依頁面提示手動處理；遇到訂金或條款畫面，也需在 Chrome 繼續操作。網站未提供完整可訂日期時，程式會提示掃描可能不完整。

## Email 與簡訊驗證

在「Email 設定」填入 SMTP 主機、連接埠、加密方式、帳號及密碼，按「寄送測試信」確認。收件人可填多位，以換行、逗號或分號分隔。

使用 Gmail 時，SMTP 設定為 `smtp.gmail.com`、連接埠 `465`、加密 `SSL/TLS`，密碼請使用 [Google 應用程式密碼](https://support.google.com/accounts/answer/185833?hl=zh-Hant)。

簡訊驗證可直接在 Chrome 輸入，或啟用 Gmail 回信驗證：

1. 在「Email 設定」勾選「允許用 Gmail 回信完成簡訊驗證」，寄送測試信確認後儲存。收信使用 `imap.gmail.com:993`，共用寄件 Gmail 的帳號與應用程式密碼。
2. 勾選「Email 通知」與「自動訂位」後開始監看。出現簡訊驗證欄位時，通知會寄到**寄件 Gmail 帳號**。
3. 用同一 Gmail 帳號回覆原信，**第一行只填簡訊上的 4 位數字**。請在 inline 的驗證期限內回信，並保持程式與原 Chrome 頁面開啟；程式最多等待 3 分鐘。

若回信逾時或驗證失敗，請回到 Chrome 處理。回信驗證預設關閉。

## 設定與資料

「訂位資料」與「Email 設定」按「儲存」後，下次開啟會自動帶入。若要保存 SMTP 密碼，需勾選「記住應用程式密碼」；取消勾選後再次儲存即可移除。

安裝版的資料位於 `%LOCALAPPDATA%\IslandInlineWatcher`：

- `.env`：設定與聯絡資料，記住的密碼以明文儲存，請勿分享或提交到 Git。
- `logs`：查詢紀錄，可用於回報問題。
- `.browser-native-profile`：程式專用的 Chrome 資料。

覆蓋更新與解除安裝會保留資料。若要完全移除，請先關閉程式及其 Chrome，再刪除上述資料夾。

從 BAT 版改用安裝版時，關閉兩邊程式，將舊專案的 `.env` 複製到上述資料夾；已有設定時請先備份。

## 從原始碼啟動

```powershell
git clone https://github.com/sfy257000/island-inline-watcher.git
cd island-inline-watcher
.\start.bat
```

也可以下載 ZIP，完整解壓縮後雙擊 `start.bat`。首次啟動需連網，自動準備 Python 與所需套件。原始碼版的設定和資料保存在專案資料夾內。

已安裝 uv 時，可直接執行：

```powershell
uv run --locked --managed-python --python 3.12 python island_inline_watcher.py
```

## 開發與測試

在已安裝 Google Chrome 的 Windows 環境執行回歸測試；測試使用模擬頁面，不會送出真實訂位或寄信。

```powershell
uv run --locked --managed-python --python 3.12 python -m unittest discover -s tests -v
```

單日診斷只查詢、不寄信或送出訂位；加上 `--summary` 可核對訂位摘要，`--keep-open` 可保留 Chrome。日期請自行替換：

```powershell
uv run --locked --managed-python --python 3.12 python diagnose_inline.py --date 2026-11-19 --summary
```

安裝檔建置與新版發佈方式見 [Windows 打包與發佈](docs/windows-packaging.md)。

## 歡迎貢獻

歡迎透過 [Issues](https://github.com/sfy257000/island-inline-watcher/issues) 回報問題、提出想法，或提交 [Pull Request](https://github.com/sfy257000/island-inline-watcher/pulls) 改善程式、測試與文件。較大的功能調整建議先開 Issue 討論。

PR 請說明修改內容與測試結果；回報錯誤時請提供版本、重現步驟及錯誤紀錄，並遮蔽個資、密碼、Cookie 和驗證碼。

## License

本專案採用 [MIT License](LICENSE)。第三方元件適用各自授權；Inno Setup 繁體中文語系的來源與授權見 [打包文件](docs/windows-packaging.md#第三方語系檔)。
