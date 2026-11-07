using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Threading;

namespace SectorTray
{
    /// <summary>Finds SectorTray's files, starts the Python server hidden, and makes sure it dies with us.</summary>
    internal sealed class Server : IDisposable
    {
        public const int Port = 8742;  // fixed so the web UI's saved preferences (skin, units) stay put
        public static string Url => $"http://127.0.0.1:{Port}/";

        public string Root { get; }
        public string Python { get; }
        public string LogPath { get; }
        Process _proc;
        IntPtr _job = IntPtr.Zero;

        Server(string root, string python)
        {
            Root = root;
            Python = python;
            var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SectorTray");
            Directory.CreateDirectory(dir);
            LogPath = Path.Combine(dir, "server.log");
        }

        /// <summary>
        /// Portable layout:  SectorTray.exe, python\python.exe, app\sectortray\…
        /// Development:      any folder under the repo (walks up to find sectortray\__main__.py), python on PATH.
        /// </summary>
        public static Server Locate()
        {
            var exeDir = AppDomain.CurrentDomain.BaseDirectory;
            var portableApp = Path.Combine(exeDir, "app");
            var portablePy = Path.Combine(exeDir, "python", "python.exe");
            if (File.Exists(Path.Combine(portableApp, "sectortray", "__main__.py")) && File.Exists(portablePy))
                return new Server(portableApp, portablePy);
            for (var d = new DirectoryInfo(exeDir); d != null; d = d.Parent)
                if (File.Exists(Path.Combine(d.FullName, "sectortray", "__main__.py")))
                    return new Server(d.FullName, Environment.GetEnvironmentVariable("SECTORTRAY_PYTHON") ?? "python");
            throw new FileNotFoundException("SectorTray's files weren't found next to SectorTray.exe.");
        }

        public static bool IsUp()
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(Url + "api/health");
                req.Timeout = 1500;
                req.Proxy = null;
                using (var res = (HttpWebResponse)req.GetResponse())
                using (var r = new StreamReader(res.GetResponseStream()))
                    return r.ReadToEnd().Contains("\"sectortray\"");
            }
            catch (Exception) { return false; }
        }

        /// <summary>The server's data folder (where drawers and exports live), from /api/health. Null if unknown.
        /// Asked each time: Settings can move the folder while the app runs.</summary>
        public string DataDir()
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(Url + "api/health");
                req.Timeout = 1500;
                req.Proxy = null;
                using (var res = (HttpWebResponse)req.GetResponse())
                using (var r = new StreamReader(res.GetResponseStream()))
                {
                    var m = System.Text.RegularExpressions.Regex.Match(r.ReadToEnd(), @"""data_dir""\s*:\s*""((?:[^""\\]|\\.)*)""");
                    if (!m.Success) return null;
                    return System.Text.RegularExpressions.Regex.Unescape(m.Groups[1].Value);
                }
            }
            catch (Exception) { return null; }
        }

        /// <summary>Starts the server unless one is already running (e.g. from SectorTray.cmd). Waits until it answers.</summary>
        public void Start(TimeSpan timeout)
        {
            if (IsUp()) return;
            var psi = new ProcessStartInfo(Python, $"-m sectortray --no-browser --port {Port}")
            {
                WorkingDirectory = Root,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            var log = new StreamWriter(LogPath, false) { AutoFlush = true };
            _proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
            _proc.OutputDataReceived += (s, e) => { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
            _proc.ErrorDataReceived += (s, e) => { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
            _proc.Start();
            _proc.BeginOutputReadLine();
            _proc.BeginErrorReadLine();
            KillWithUs(_proc);

            var until = DateTime.UtcNow + timeout;
            while (DateTime.UtcNow < until)
            {
                if (_proc.HasExited) throw new InvalidOperationException("The SectorTray server stopped while starting.");
                if (IsUp()) return;
                Thread.Sleep(150);
            }
            throw new TimeoutException("The SectorTray server didn't answer in time.");
        }

        public string LogTail(int lines = 12)
        {
            try
            {
                using (var fs = new FileStream(LogPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (var r = new StreamReader(fs))
                {
                    var all = r.ReadToEnd().Replace("\r", "").Split('\n');
                    var start = Math.Max(0, all.Length - lines);
                    return string.Join(Environment.NewLine, all, start, all.Length - start).Trim();
                }
            }
            catch (Exception) { return ""; }
        }

        public void Dispose()
        {
            try { if (_proc != null && !_proc.HasExited) _proc.Kill(); } catch (Exception) { }
            if (_job != IntPtr.Zero) { CloseHandle(_job); _job = IntPtr.Zero; }  // closing the job also kills the server
        }

        // A Windows job object with KILL_ON_JOB_CLOSE: if this process ends for any reason (even a crash),
        // Windows closes the job handle and ends the Python server too, so it never lingers in the background.
        void KillWithUs(Process p)
        {
            _job = CreateJobObject(IntPtr.Zero, null);
            if (_job == IntPtr.Zero) return;
            var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            info.BasicLimitInformation.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            int len = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
            IntPtr ptr = Marshal.AllocHGlobal(len);
            try
            {
                Marshal.StructureToPtr(info, ptr, false);
                SetInformationJobObject(_job, 9 /* JobObjectExtendedLimitInformation */, ptr, (uint)len);
            }
            finally { Marshal.FreeHGlobal(ptr); }
            AssignProcessToJobObject(_job, p.Handle);
        }

        [StructLayout(LayoutKind.Sequential)]
        struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass, SchedulingClass;
        }
        [StructLayout(LayoutKind.Sequential)]
        struct IO_COUNTERS { public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount, ReadTransferCount, WriteTransferCount, OtherTransferCount; }
        [StructLayout(LayoutKind.Sequential)]
        struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr a, string name);
        [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint len);
        [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    }
}
