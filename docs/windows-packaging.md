# Windows 打包與發佈

## 本機建置

在 Windows x64 安裝 Git、uv 與 Inno Setup 6.5 以上，再從專案根目錄執行：

```powershell
.\build_windows.ps1
```

新增檔案需先 `git add`，建置使用 Git 已追蹤的檔案封裝原始碼。找不到 Inno Setup 時，可指定編譯器：

```powershell
.\build_windows.ps1 -InnoSetupCompiler 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

產物位於 `release`：

- `island-inline-watcher-v版本-windows-x64-setup.exe`：一般使用者安裝檔，內含 Python 與所需套件，仍需另裝 Google Chrome。
- `island-inline-watcher-v版本-windows-source.zip`：解壓縮後可用 `start.bat` 啟動的原始碼。
- `SHA256SUMS.txt`：下載檔的 SHA-256。

`build`、`dist` 與 `release` 為建置產物，不需提交到 Git。安裝與資料保存方式見 [README](../README.md#設定與資料)。

## GitHub Actions 與新版發佈

PR、推送至 `codex/**` 分支或手動執行 `Build Windows installer` 時，會建置安裝檔並執行回歸、安裝與覆蓋更新測試。完成後從該次執行的 Artifacts 下載 `windows-release`；測試報告位於 `windows-validation`。

發佈新版本時：

1. 同步更新 `pyproject.toml` 的版本、程式的 `APP_VERSION`、README 版本與下載檔名，並用 `uv lock` 更新鎖定檔。
2. 將 `RELEASE_NOTES.md` 改為新版本的使用者變更摘要。
3. 合併至 `main`，`Publish release` 通過 Windows 建置與測試後，會建立對應版本的 Release 並附上下載檔。

請使用新的版本號；已發佈的標籤不會覆寫。只合併一般功能或修正、未更新發佈檔案時，不會自動發佈新 EXE。

自動測試使用模擬頁面，未涵蓋真實訂位送出或 Gmail 收發。`tools/test_installer.ps1` 只供可拋棄的 CI runner 使用，請勿在日常電腦執行。

## 簽章與檔案核對

預設安裝檔未簽章，可能出現 SmartScreen 提示。若已有受信任的程式碼簽章憑證，將憑證放在 Windows 目前使用者的 `My` 憑證存放區，安裝 Windows SDK 的 SignTool 後執行：

```powershell
.\build_windows.ps1 -CertificateThumbprint '你的40位憑證指紋'
```

可用 `-SignToolPath` 與 `-TimestampUrl` 指定工具和時間戳記服務。請勿將憑證私鑰或密碼提交到 Git。

核對下載檔雜湊：

```powershell
Get-FileHash .\island-inline-watcher-v1.04-windows-x64-setup.exe -Algorithm SHA256
```

CI 的 Defender 掃描結果記錄於 `windows-validation`；`unavailable` 代表未完成掃描。若偵測到威脅，請保留偵測名稱與檔案雜湊，確認原因後再發佈。雜湊一致不代表防毒認證。

## 第三方語系檔

`packaging/ChineseTraditional.isl` 來自 [Inno Setup 繁體中文翻譯](https://github.com/jrsoftware/issrc/blob/main/Files/Languages/ChineseTraditional.isl)，作者與貢獻者列於檔頭，授權見 [InnoSetup-LICENSE.txt](../packaging/InnoSetup-LICENSE.txt)。
