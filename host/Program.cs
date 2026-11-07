using System;
using System.Threading;
using System.Windows.Forms;

namespace SectorTray
{
    internal static class Program
    {
        const string MutexName = @"Local\SectorTray.App";
        const string ShowEventName = @"Local\SectorTray.Show";

        [STAThread]
        static void Main()
        {
            // One SectorTray at a time: a second launch just brings the open window to the front.
            using (var mutex = new Mutex(true, MutexName, out bool first))
            {
                if (!first)
                {
                    try { using (var ev = EventWaitHandle.OpenExisting(ShowEventName)) ev.Set(); } catch (Exception) { }
                    return;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);

                Server server;
                try { server = Server.Locate(); }
                catch (Exception ex)
                {
                    MessageBox.Show(ex.Message, "SectorTray", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }

                using (var show = new EventWaitHandle(false, EventResetMode.AutoReset, ShowEventName))
                using (var form = new MainForm(server))
                {
                    var watcher = new Thread(() =>
                    {
                        while (show.WaitOne())
                            try
                            {
                                form.BeginInvoke((Action)(() =>
                                {
                                    if (form.WindowState == FormWindowState.Minimized) form.WindowState = FormWindowState.Normal;
                                    form.Activate();
                                }));
                            }
                            catch (Exception) { return; }  // form gone
                    }) { IsBackground = true };
                    watcher.Start();
                    Application.Run(form);
                }
                GC.KeepAlive(mutex);
            }
        }
    }
}
