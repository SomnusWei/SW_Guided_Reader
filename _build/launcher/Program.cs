using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;

class Program
{
    const int Port = 8777;
    static string Root = AppContext.BaseDirectory;          // exe 放在 学习工具平台/ 下
    static string Url = $"http://127.0.0.1:{Port}/index.html";

    [DllImport("user32.dll")]
    static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    static int Main(string[] args)
    {
        bool stop = args.Length > 0 && args[0] == "stop";

        if (stop) { StopServer(); return 0; }

        if (!IsPortOpen())
        {
            StartServer();
            // 等服务器就绪（最多 10 秒）
            for (int i = 0; i < 50; i++)
            {
                if (IsPortOpen()) break;
                System.Threading.Thread.Sleep(200);
            }
        }
        OpenBrowser(Url);
        return 0;
    }

    static bool IsPortOpen()
    {
        try
        {
            using var c = new TcpClient();
            var r = c.BeginConnect("127.0.0.1", Port, null, null);
            if (!r.AsyncWaitHandle.WaitOne(300)) { c.Close(); return false; }
            c.EndConnect(r);
            c.Close();
            return true;
        }
        catch { return false; }
    }

    static void StartServer()
    {
        // 定位 Python：优先随目录附带的 pythonw.ini 指定路径，其次 PATH，最后 WorkBuddy 托管路径
        string py = FindPython();
        if (py == null) throw new Exception("未找到 python.exe，请在启动平台.exe 同目录创建 pythonw.ini 写入 python.exe 完整路径");

        var psi = new ProcessStartInfo
        {
            FileName = py,
            // 由 serve.py 提供静态文件 + Reader 拉起接口（/api/reader*）
            Arguments = $"\"{Path.Combine(FindRoot(), "serve.py")}\" {Port}",
            WorkingDirectory = FindRoot(),
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
        Process.Start(psi);
    }

    static void StopServer()
    {
        // 杀掉监听 8777 的进程
        try
        {
            var psi = new ProcessStartInfo("cmd.exe", "/c netstat -ano | findstr :8777 | findstr LISTENING")
            { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true };
            using var p = Process.Start(psi);
            string outp = p.StandardOutput.ReadToEnd();
            p.WaitForExit();
            foreach (var line in outp.Split('\n'))
            {
                var parts = line.Trim().Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length >= 5 && int.TryParse(parts[^1], out int pid))
                {
                    try { Process.GetProcessById(pid).Kill(); } catch { }
                }
            }
        }
        catch { }
    }

    static string FindRoot()
    {
        // exe 应位于 学习工具平台/ 根；向下找 index.html，找不到则用 exe 所在目录
        string dir = AppContext.BaseDirectory;
        if (File.Exists(Path.Combine(dir, "index.html"))) return dir;
        var parent = Directory.GetParent(dir.TrimEnd(Path.DirectorySeparatorChar));
        if (parent != null && File.Exists(Path.Combine(parent.FullName, "index.html"))) return parent.FullName;
        return dir;
    }

    static string FindPython()
    {
        string ini = Path.Combine(AppContext.BaseDirectory, "pythonw.ini");
        if (File.Exists(ini))
        {
            string p = File.ReadAllText(ini).Trim().Trim('"');
            if (File.Exists(p)) return p;
        }
        // WorkBuddy 托管 Python
        string managed = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            @".workbuddy\binaries\python\versions\3.13.12\python.exe");
        if (File.Exists(managed)) return managed;
        // PATH
        var psi = new ProcessStartInfo("cmd.exe", "/c where python")
        { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true };
        using var pr = Process.Start(psi);
        string o = pr.StandardOutput.ReadToEnd();
        pr.WaitForExit();
        foreach (var line in o.Split('\n'))
        {
            var t = line.Trim();
            if (t.EndsWith("python.exe") && File.Exists(t)) return t;
        }
        return null;
    }

    static void OpenBrowser(string url)
    {
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
        catch { }
    }
}
