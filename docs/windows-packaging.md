# Windows 打包與發佈

## 產物與執行環境

- 支援 Windows 10／11 x64；需另行安裝 Google Chrome。
- `island-inline-watcher-v版本-windows-x64-setup.exe`：唯一需要交給一般使用者的安裝檔。內含 Python 3.12、Tkinter、python-dotenv、websocket-client；不包含 Playwright、Chrome、個人設定或瀏覽器資料。
- `island-inline-watcher-v版本-windows-source.zip`：完整解壓縮後可用 `start.bat` 啟動的原始碼版。
- `SHA256SUMS.txt`：上述兩個下載檔的 SHA-256。

採用 PyInstaller onedir 加上 Inno Setup。單一安裝檔內含多個執行期檔案，不需要每次開啟時將整個 Python 環境解壓縮到暫存目錄。安裝版不會自行下載套件，也不會自動更新；使用者可下載新版安裝檔覆蓋安裝。

執行程式不需要管理員權限。安裝位置是 `%LOCALAPPDATA%\Programs\IslandInlineWatcher`；使用者資料目錄是 `%LOCALAPPDATA%\IslandInlineWatcher`。解除安裝會保留資料（其中可能包含明文 SMTP 應用程式密碼），需完整移除時請關閉程式及其 Chrome 後自行刪除資料目錄。BAT 版保持原有專案內 `.env`、`logs`、`.browser-native-profile`。

## 本機建置

在 Windows x64 安裝 Git、uv 與 Inno Setup 6.5 以上或 7，再從專案根目錄執行：

```powershell
.\build_windows.ps1
```

腳本使用 `uv.lock` 安裝固定依賴、產生含版本資訊的 GUI 程式、檢查打包內容、編譯安裝程式、封裝 Git 已追蹤的原始碼，再產生雜湊。新增檔案請先 `git add`，否則原始碼封裝會拒絕缺少必要檔案的狀態。產物在 `release`，中間產物在 `build`、`dist`。不要把這些目錄提交到 Git。

找不到編譯器時可明確指定：

```powershell
.\build_windows.ps1 -InnoSetupCompiler 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

建置依賴在 `build` dependency group，BAT 啟動不會安裝 PyInstaller。

## GitHub Actions

`Build Windows installer` 會在 PR、`codex/**` 分支推送或手動執行時，以 Windows 2022／Python 3.12 x64：

1. 安裝固定執行期依賴。
2. 使用原生 Chrome 控制器，對攔截的模擬頁執行回歸測試；不操作正式訂位網站、不寄信。
3. 編譯程式及安裝檔，檢查不包含 `.env`、Chrome 個人資料、測試或 Playwright。
4. 安裝到含空白與中文字元的路徑。
5. 執行離線測試：Tkinter 介面、設定儲存讀取、紀錄、TLS 初始化、原生 Chrome 開頁／輸入／選單／點擊，以及確認沒有 Playwright。
6. 確認一般 GUI 能開啟並關閉、桌面及開始功能表捷徑、解除安裝登錄。
7. 覆蓋安裝並核對設定與 Chrome 資料標記保留，再解除安裝核對程式／捷徑移除與資料保留。
8. 嘗試以 Microsoft Defender 掃描最終下載檔與解壓後執行期；記錄病毒碼版本與結果。若服務不可用，明確記錄 unavailable；若掃描偵測或執行失敗，停止提供下載產物。
9. 上傳 `windows-release`（下載產物）及 `windows-validation`（測試報告／安裝紀錄／簽章狀態）。

`tools/test_installer.ps1` 僅允許在可拋棄的 GitHub Actions runner 執行，避免破壞本機的安裝或設定。覆蓋安裝測試重裝同一版本；仍建議正式發布前人工確認從前一個已發布安裝版升級的行為。

`Publish release` 保留原有 main 分支版本／Release notes 觸發方式；先呼叫上述完整 Windows 建置測試，再核對雜湊，全部通過後才建立 GitHub Release。請保持 `pyproject.toml`、程式 `APP_VERSION` 與 `RELEASE_NOTES.md` 版本一致；既有標籤不會自動覆寫。

## 簽章

預設產物未簽章，CI 報告會如實記錄。程式中的 `AppPublisher` 與 EXE 的 `CompanyName` 是文字資訊，並不代表身分已被憑證機構驗證。

若已有可用於 Authenticode 的受信任程式碼簽章憑證，且私鑰可透過 Windows 目前使用者的 `My` 憑證存放區使用（硬體金鑰／雲端 HSM 需先完成供應商設定），安裝 Windows SDK 的 SignTool 後可執行：

```powershell
.\build_windows.ps1 -CertificateThumbprint '你的40位憑證指紋'
```

可另外指定 `-SignToolPath` 與 `-TimestampUrl`。腳本會簽署主 EXE，透過 Inno Setup 簽署解除安裝程式與安裝程式，並驗證 EXE 及安裝檔的簽章。雜湊在所有簽章完成後才產生。不要把憑證私鑰、PFX 或密碼提交到 Git；目前 workflow 不接收這類秘密，也不會替你申請／購買憑證。自簽憑證不能替一般下載者建立公開信任。

## Windows 提示與防毒複核

未簽章或信譽不足可能觸發 SmartScreen；即使有受信任簽章，也不能保證新程式不顯示提示。Inno Setup 不會自行賦予簽章。PyInstaller 產物也可能遭防毒誤判，不能以打包參數保證解決。

SHA-256 用於核對下載檔與公布的版本是否一致，並非安全認證：

```powershell
Get-FileHash .\island-inline-watcher-v1.05-windows-x64-setup.exe -Algorithm SHA256
```

請在發布前以更新過的防毒掃描**最終**安裝檔與安裝後程式。GitHub runner 的建置與功能測試不等同防毒認證。`windows-validation/defender.json` 會記錄實際掃描結果與病毒碼；unavailable 表示未掃描，不可解讀成沒有病毒，單一引擎通過也不保證其他引擎的判定。未記錄的掃描一律視為未驗證。若有威脅偵測，先保留檔案 SHA-256、版本、偵測名稱及防毒版本，檢查依賴與建置來源，再向偵測廠商提交複核，確認後才發佈。SmartScreen 的信譽提示與具體惡意軟體偵測必須分開處理；不要要求使用者關閉防毒或加入排除清單。

Microsoft 複核入口：https://www.microsoft.com/en-us/wdsi/filesubmission

## 驗收範圍

自動測試覆蓋打包相依、離線瀏覽器操作及安裝生命週期，不會登入 Gmail、寄信、操作真人驗證或送出真實訂位。真實網站及 SMTP／IMAP 的最終行為仍取決於現場網路、帳號與網站狀態。

## 第三方語系檔

`packaging/ChineseTraditional.isl` 為 Inno Setup 6.5.0+ 的繁體中文翻譯，原作者與貢獻者列在檔頭，來源為 https://github.com/jrsoftware/issrc/blob/main/Files/Languages/ChineseTraditional.isl （Git blob `2976928a652a82d819c46e69a9b91d82639cca51`）。此檔未修改，隨附原專案的 `packaging/InnoSetup-LICENSE.txt`；固定納入專案，避免建置時依賴 runner 是否安裝額外語系。

## 專屬 EXE 圖示

`assets/island-inline-watcher.png` 是 256×256 透明背景圖示，供程式視窗使用。`assets/island-inline-watcher.ico` 內含 16、24、32、48、64、128、256 像素版本，供 PyInstaller 的 `icon` 與 Inno Setup 的 `SetupIconFile` 共用。桌面及開始功能表捷徑直接取用程式 EXE 的圖示。建置不需要額外安裝 Pillow 或 ImageMagick。

更新圖示時請同步替換 PNG 與多尺寸 ICO。若已有新的正方形 PNG，可使用 ImageMagick 匯出格式（圖像內容由原始設計決定）：

```powershell
magick input.png -resize 256x256 -strip assets/island-inline-watcher.png
magick assets/island-inline-watcher.png -define icon:auto-resize=256,128,64,48,32,24,16 -define icon:png-compression-size=256 assets/island-inline-watcher.ico
```

`build_windows.ps1` 會以 `tools/check_exe_icons.py` 核對最終安裝檔與程式本體 PE 資源中的圖示尺寸及每張圖示的 SHA-256，結果存於 `build/validation/icons.json`；實際安裝與覆蓋安裝的離線測試也會載入視窗 PNG，避免資源漏包。
