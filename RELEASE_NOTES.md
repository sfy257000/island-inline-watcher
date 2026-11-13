# v1.04

新增 Windows 10／11 x64 單檔安裝程式。下載 `island-inline-watcher-v1.04-windows-x64-setup.exe`，安裝後從桌面或開始功能表開啟；不需另外安裝 Python、uv 或 Playwright，仍需 Google Chrome。

- 安裝於目前使用者帳號，不要求系統管理員權限，提供捷徑及解除安裝功能。
- 安裝版設定、紀錄與 Chrome 資料存於 `%LOCALAPPDATA%\IslandInlineWatcher`，覆蓋更新及解除安裝會保留；原始碼／BAT 版維持專案內原有路徑。
- 移除 Playwright 執行時依賴，錯誤處理改用共用例外類別；網頁操作維持既有原生 Chrome 控制器。
- 原始碼啟動方式保留在 `island-inline-watcher-v1.04-windows-source.zip`。
- CI 執行原生 Chrome 回歸測試、打包內容檢查、離線 GUI／Chrome 測試，以及安裝／覆蓋安裝／解除安裝與資料保留測試。
- 提供 `SHA256SUMS.txt`，並可使用自己的受信任憑證簽署程式、安裝檔與解除安裝程式。

目前預設產物未簽章，可能顯示 SmartScreen 或被防毒誤判；不承諾免警告。請從官方 Release 下載並核對雜湊，具體威脅偵測請回報供複核，不要關閉防護。

舊 BAT 版改用安裝版時，先關閉程式與其 Chrome，再將舊專案的 `.env` 複製至 `%LOCALAPPDATA%\IslandInlineWatcher\.env`。已有新設定時先備份。原本的監看、多人 Email 通知、Gmail 回信驗證及 v1.03 日曆修正均保留。
