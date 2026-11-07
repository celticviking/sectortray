using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace SectorTray
{
    /// <summary>
    /// A window with no Windows title bar: the web UI's header is the title bar (CSS app-region: drag) and draws
    /// its own minimise / maximise / close buttons, which talk to this form through window.chrome.webview.
    /// The left, right and bottom frame stay native (invisible on Windows 10/11), so resizing, the drop shadow,
    /// Aero snap and Win+arrow keys keep working.
    /// </summary>
    internal sealed class MainForm : Form
    {
        readonly Server _server;
        readonly WebView2 _web = new WebView2 { Dock = DockStyle.Fill, Visible = false };
        readonly Label _status = new Label
        {
            Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, ForeColor = Color.FromArgb(0xEC, 0xEB, 0xE7),
            Font = new Font("Segoe UI", 11f), Text = "Starting SectorTray…",
        };

        public MainForm(Server server)
        {
            _server = server;
            Text = "SectorTray";
            BackColor = Color.FromArgb(0x17, 0x19, 0x1B);
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(1100, 700);
            Size = Screen.PrimaryScreen.WorkingArea.Width >= 1600 ? new Size(1520, 940) : new Size(1280, 820);
            if (File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "SectorTray.ico")))
                Icon = new Icon(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "SectorTray.ico"));
            else
                Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            Controls.Add(_web);
            Controls.Add(_status);
            Load += async (s, e) => await StartAsync();
            Resize += (s, e) => SendWindowState();
            Activated += (s, e) => SendWindowState();
            Deactivate += (s, e) => SendWindowState();
        }

        async Task StartAsync()
        {
            try
            {
                await Task.Run(() => _server.Start(TimeSpan.FromSeconds(30)));
            }
            catch (Exception ex)
            {
                var tail = _server.LogTail();
                MessageBox.Show(this, ex.Message + (tail.Length > 0 ? "\n\n" + tail : "") + "\n\nFull log: " + _server.LogPath,
                    "SectorTray couldn't start", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
                return;
            }

            var data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SectorTray", "WebView2");
            CoreWebView2Environment env;
            try
            {
                env = await CoreWebView2Environment.CreateAsync(null, data);
                await _web.EnsureCoreWebView2Async(env);
            }
            catch (WebView2RuntimeNotFoundException)
            {
                if (MessageBox.Show(this, "SectorTray needs Microsoft's WebView2 Runtime, which comes with Windows 11 but is missing here.\n\nOpen SectorTray in your web browser instead?",
                        "WebView2 Runtime not found", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes)
                    OpenExternal(Server.Url);
                Close();
                return;
            }

            var core = _web.CoreWebView2;
            var settings = core.Settings;
            settings.IsStatusBarEnabled = false;
            settings.IsGeneralAutofillEnabled = false;
            settings.IsPasswordAutosaveEnabled = false;
#if !DEBUG
            settings.AreDevToolsEnabled = false;
#endif
            try { settings.IsNonClientRegionSupportEnabled = true; } catch (Exception) { /* older runtime: the page falls back to asking us to drag */ }

            core.WebMessageReceived += OnMessage;
            core.DocumentTitleChanged += (s, e) => Text = string.IsNullOrWhiteSpace(core.DocumentTitle) ? "SectorTray" : core.DocumentTitle;
            core.NavigationCompleted += (s, e) => SendWindowState();
            // Keep the window on SectorTray's own pages; anything else opens in the normal browser.
            core.NavigationStarting += (s, e) => { if (!IsOwn(e.Uri)) { e.Cancel = true; OpenExternal(e.Uri); } };
            core.NewWindowRequested += (s, e) => { e.Handled = true; if (IsOwn(e.Uri)) core.Navigate(e.Uri); else OpenExternal(e.Uri); };

            _web.Visible = true;
            _status.Visible = false;
            core.Navigate(Server.Url);
        }

        static bool IsOwn(string uri) => uri.StartsWith(Server.Url, StringComparison.OrdinalIgnoreCase);

        static void OpenExternal(string uri)
        {
            if (uri.StartsWith("http://", StringComparison.OrdinalIgnoreCase) || uri.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                try { Process.Start(new ProcessStartInfo(uri) { UseShellExecute = true }); } catch (Exception) { }
        }

        /* ---------------- page ⇄ window ---------------- */
        void OnMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            string cmd;
            try { cmd = e.TryGetWebMessageAsString(); } catch (Exception) { return; }
            if (cmd != null && cmd.StartsWith("reveal:", StringComparison.Ordinal)) { Reveal(cmd.Substring(7)); return; }
            // Settings → Saved drawers → Move…: say we got it (else the page offers to type a folder), then open
            // the picker once this handler has returned (a modal dialog inside a WebView2 event re-enters it)
            if (cmd == "pickfolder")
            {
                _web.CoreWebView2?.PostWebMessageAsJson("{\"type\":\"folder-open\"}");
                BeginInvoke((Action)PickFolder);
                return;
            }
            if (cmd != null && cmd.StartsWith("zoom:", StringComparison.Ordinal))  // Settings → Text size
            {
                if (double.TryParse(cmd.Substring(5), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var z) && z >= 0.5 && z <= 2)
                    _web.ZoomFactor = z;
                return;
            }
            switch (cmd)
            {
                case "minimize": WindowState = FormWindowState.Minimized; break;
                case "maximize": WindowState = FormWindowState.Maximized; break;
                case "restore": WindowState = FormWindowState.Normal; break;
                case "toggle-maximize": WindowState = WindowState == FormWindowState.Maximized ? FormWindowState.Normal : FormWindowState.Maximized; break;
                case "close": Close(); break;
                case "drag":  // fallback for runtimes without app-region support
                    ReleaseCapture();
                    SendMessage(Handle, WM_NCLBUTTONDOWN, (IntPtr)HTCAPTION, IntPtr.Zero);
                    break;
                case "hello": SendWindowState(); break;
            }
        }

        [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int processId);
        const int ASFW_ANY = -1;

        /// <summary>Opens a folder in Explorer from this (foreground) window so it comes to the front.
        /// Only folders inside the server's data folder, which the page can't choose freely.</summary>
        void Reveal(string path)
        {
            try
            {
                var dir = Path.GetFullPath(path);
                var root = _server.DataDir();
                if (root == null || !Directory.Exists(dir)) return;
                root = Path.GetFullPath(root).TrimEnd('\\') + "\\";
                if (!(dir.TrimEnd('\\') + "\\").StartsWith(root, StringComparison.OrdinalIgnoreCase)) return;
                AllowSetForegroundWindow(ASFW_ANY);
                Process.Start(new ProcessStartInfo("explorer.exe", "\"" + dir + "\"") { UseShellExecute = true });
            }
            catch (Exception) { }
        }

        /// <summary>Windows' folder picker; the page gets {type:"folder", path} (path null when canceled).</summary>
        void PickFolder()
        {
            var core = _web.CoreWebView2;
            if (core == null) return;
            string path = null;
            using (var dlg = new FolderBrowserDialog { Description = "Where should SectorTray save your drawers?", ShowNewFolderButton = true })
                if (dlg.ShowDialog(this) == DialogResult.OK) path = dlg.SelectedPath;
            var json = path == null ? "null" : "\"" + path.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
            core.PostWebMessageAsJson("{\"type\":\"folder\",\"path\":" + json + "}");
        }

        void SendWindowState()
        {
            var core = _web.CoreWebView2;
            if (core == null) return;
            var state = WindowState == FormWindowState.Maximized ? "maximized" : "normal";
            var active = ActiveForm == this ? "true" : "false";
            var dragRegions = core.Settings.IsNonClientRegionSupportEnabled ? "true" : "false";
            core.PostWebMessageAsJson($"{{\"type\":\"window\",\"state\":\"{state}\",\"active\":{active},\"appRegion\":{dragRegions}}}");
        }

        /* ---------------- frame: drop the caption, keep native resize borders ---------------- */
        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            var m = new MARGINS { cyTopHeight = 1 };  // keeps the DWM shadow along the top edge
            DwmExtendFrameIntoClientArea(Handle, ref m);
            SetWindowPos(Handle, IntPtr.Zero, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED);
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WM_NCCALCSIZE && m.WParam != IntPtr.Zero)
            {
                var before = (NCCALCSIZE_PARAMS)Marshal.PtrToStructure(m.LParam, typeof(NCCALCSIZE_PARAMS));
                int top = before.rgrc0.top;
                base.WndProc(ref m);  // default: subtract caption + borders
                var after = (NCCALCSIZE_PARAMS)Marshal.PtrToStructure(m.LParam, typeof(NCCALCSIZE_PARAMS));
                // Take back the caption and top border. When maximised, Windows places the window a frame-width
                // beyond the screen edge, so keep that much at the top or the header would be cut off.
                after.rgrc0.top = top + (WindowState == FormWindowState.Maximized ? FrameThickness() : 0);
                Marshal.StructureToPtr(after, m.LParam, false);
                m.Result = IntPtr.Zero;
                return;
            }
            base.WndProc(ref m);
        }

        int FrameThickness()
        {
            uint dpi = GetDpiForWindow(Handle);
            return GetSystemMetricsForDpi(SM_CYFRAME, dpi) + GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi);
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            _server.Dispose();
            base.OnFormClosed(e);
        }

        const int WM_NCCALCSIZE = 0x0083, WM_NCLBUTTONDOWN = 0x00A1, HTCAPTION = 2, SM_CYFRAME = 33, SM_CXPADDEDBORDER = 92;
        const uint SWP_NOSIZE = 0x1, SWP_NOMOVE = 0x2, SWP_NOZORDER = 0x4, SWP_FRAMECHANGED = 0x20;
        [StructLayout(LayoutKind.Sequential)] struct RECT { public int left, top, right, bottom; }
        [StructLayout(LayoutKind.Sequential)] struct NCCALCSIZE_PARAMS { public RECT rgrc0, rgrc1, rgrc2; public IntPtr lppos; }
        [StructLayout(LayoutKind.Sequential)] struct MARGINS { public int cxLeftWidth, cxRightWidth, cyTopHeight, cyBottomHeight; }
        [DllImport("dwmapi.dll")] static extern int DwmExtendFrameIntoClientArea(IntPtr hwnd, ref MARGINS m);
        [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
        [DllImport("user32.dll")] static extern bool ReleaseCapture();
        [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
        [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr h);
        [DllImport("user32.dll")] static extern int GetSystemMetricsForDpi(int index, uint dpi);
    }
}
