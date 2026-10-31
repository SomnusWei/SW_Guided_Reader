use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, State, WebviewUrl, WebviewWindowBuilder};

/// Reader sidecar 的运行状态：子进程句柄 + READY URL
pub struct ReaderState {
    child: Mutex<Option<Child>>,
    url: Mutex<Option<String>>,
    /// 串行化启动流程：前端多个入口（iframe、状态灯、按钮）会同时触发，
    /// 并发 spawn 会让多个 sidecar 抢同一个数据目录，必然有实例启动失败。
    start_lock: Mutex<()>,
}

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const READY_TIMEOUT: Duration = Duration::from_secs(90);

/// 定位 sidecar 目录（含 reader-service.exe 的目录）。
/// 生产环境：资源目录 reader-service/；开发环境：src-tauri/binaries/reader-service/。
fn sidecar_dir(app: &AppHandle) -> Option<PathBuf> {
    if let Ok(resource) = app.path().resource_dir() {
        let candidate = resource.join("reader-service");
        if candidate.join("reader-service.exe").exists() {
            return Some(candidate);
        }
    }
    // 开发模式：tauri dev 的 cwd 是 src-tauri
    if let Ok(cwd) = std::env::current_dir() {
        let candidate = cwd.join("binaries").join("reader-service");
        if candidate.join("reader-service.exe").exists() {
            return Some(candidate);
        }
        // cargo 直接运行时 cwd 可能是 src-tauri 的上级或 target/debug
        if let Some(parent) = cwd.parent() {
            let candidate = parent.join("binaries").join("reader-service");
            if candidate.join("reader-service.exe").exists() {
                return Some(candidate);
            }
        }
    }
    None
}

/// 数据目录策略：
/// 1) 环境变量 GUIDED_READER_DATA_DIR（开发/调试用）
/// 2) 便携模式：主程序 exe 旁的 Reader/（存在即用；父目录可写则创建）
/// 3) 兜底：%APPDATA%/LearningTools/Reader（沿用历史目录名，保住既有用户数据）
fn pick_data_dir(app: &AppHandle) -> PathBuf {
    if let Ok(env_dir) = std::env::var("GUIDED_READER_DATA_DIR") {
        if !env_dir.trim().is_empty() {
            return PathBuf::from(env_dir);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(base) = exe.parent() {
            let portable = base.join("Reader");
            if portable.is_dir() {
                return portable;
            }
            if std::fs::create_dir_all(&portable).is_ok() {
                return portable;
            }
        }
    }
    let mut fallback = app
        .path()
        .app_data_dir()
        .unwrap_or_else(|_| PathBuf::from("."));
    fallback.push("Reader");
    fallback
}

/// 2.0 技能种子目录（default-skills）：随 PyInstaller onedir 一起装在
/// `<资源目录>/reader-service/default-skills`；开发模式直接指向仓库目录。
/// 1.3.1 的 dailyread-skill 已随 M6 移除（后端不再引用）。
fn pick_default_skills_dir(app: &AppHandle) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(resource) = app.path().resource_dir() {
        candidates.push(resource.join("reader-service").join("default-skills"));
    }
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.join("default-skills"));
        candidates.push(cwd.join("408-guided-reader").join("default-skills"));
        if let Some(parent) = cwd.parent() {
            candidates.push(parent.join("default-skills"));
        }
    }
    candidates
        .into_iter()
        .find(|dir| dir.join("SKILL.md").is_file() || dir.join("yixue-zonghe").join("SKILL.md").is_file())
}

fn port_open(port: u16) -> bool {
    TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([127, 0, 0, 1], port)),
        Duration::from_millis(400),
    )
    .is_ok()
}

fn free_port() -> Result<u16, String> {
    TcpListener::bind(("127.0.0.1", 0))
        .map(|listener| listener.local_addr().map(|addr| addr.port()))
        .map_err(|error| format!("分配端口失败：{error}"))?
        .map_err(|error| format!("读取端口失败：{error}"))
}

fn kill_stale(state: &ReaderState) {
    if let Some(mut child) = state.child.lock().expect("reader child 锁").take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// 打开后端日志文件（数据目录下的 reader-service.log）。
///
/// GUI 应用没有控制台，后端出错时日志会彻底丢失；落盘后用户遇到问题可直接
/// 把这份文件发过来定位。超过 4MB 自动轮换，避免长期使用无限增长。
fn open_reader_log(data_dir: &Path) -> Option<std::fs::File> {
    let path = data_dir.join("reader-service.log");
    if let Ok(meta) = std::fs::metadata(&path) {
        if meta.len() > 4 * 1024 * 1024 {
            let _ = std::fs::remove_file(&path);
        }
    }
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .ok()?;
    let _ = writeln!(file, "\n===== SW_Guided_Reader 启动（端口随本次会话分配）=====");
    Some(file)
}

/// 持续读走一条输出流并转成文本行。
///
/// 关键点：必须用 `read_until` 按字节切行再做宽松解码。
/// 旧实现用 `BufRead::lines()`，它要求严格 UTF-8：后端只要打印一行非 UTF-8
/// 字节（Windows 管道默认 GBK 中文），读取线程就会收到 Err 并直接退出，
/// 通道随即断开，上层看到的是"进程意外退出"，而后端其实还活得好好的。
fn pump_output<R>(
    stream: R,
    sender: std::sync::mpsc::Sender<String>,
    tag: &'static str,
    log: Arc<Mutex<Option<std::fs::File>>>,
) where
    R: Read + Send + 'static,
{
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stream);
        let mut buffer = Vec::new();
        // 启动握手结束后仍要一直读：读端一旦关闭，后端下一次 print 就会写管道
        // 失败（BrokenPipe），HTTP 连接被直接掐断，浏览器表现为"未发送任何数据"。
        let mut forwarding = true;
        loop {
            buffer.clear();
            match reader.read_until(b'\n', &mut buffer) {
                Ok(0) => break, // 流已关闭
                Ok(_) => {}
                Err(_) => break,
            }
            let text = String::from_utf8_lossy(&buffer).trim().to_string();
            if text.is_empty() {
                continue;
            }
            if let Ok(mut guard) = log.lock() {
                if let Some(file) = guard.as_mut() {
                    let _ = writeln!(file, "[{tag}] {text}");
                }
            }
            if forwarding && sender.send(text).is_err() {
                forwarding = false; // 只停止转发，继续排空
            }
        }
    });
}

/// 把 sidecar 挂进"主进程关闭即全灭"的 Job Object。
/// 这样即使主程序被强杀/崩溃（走不到退出钩子），sidecar 也会被内核回收，
/// 从根上杜绝"端口被残留进程占用"的历史故障。
#[cfg(target_os = "windows")]
fn bind_kill_on_close_job(child: &Child) {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return;
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let ok = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if ok == 0 {
            return;
        }
        AssignProcessToJobObject(job, child.as_raw_handle() as *mut core::ffi::c_void);
        // 故意不关闭 job 句柄：主进程存活期间 job 生效；
        // 主进程退出（含强杀）时句柄由系统关闭，触发 kill-on-close。
    }
}

/// 确保 sidecar 运行并返回 READY URL（带 token 的本地地址）。
fn ensure_reader_running(app: &AppHandle, state: &ReaderState) -> Result<String, String> {
    // 串行化整个启动流程：并发的第二个调用会等第一个结束后直接复用其结果
    let _start_guard = state.start_lock.lock().expect("reader start 锁");
    if let Some(url) = state.url.lock().expect("reader url 锁").clone() {
        if let Some(port) = url_port(&url) {
            if port_open(port) {
                return Ok(url);
            }
        }
    }
    kill_stale(state);
    state.url.lock().expect("reader url 锁").take();

    let dir = sidecar_dir(app).ok_or_else(|| {
        "未找到 Reader 后端（reader-service.exe）。请确认安装包完整。".to_string()
    })?;
    let port = free_port()?;
    let data_dir = pick_data_dir(app);
    std::fs::create_dir_all(&data_dir)
        .map_err(|error| format!("创建数据目录失败：{error}"))?;

    let mut command = Command::new(dir.join("reader-service.exe"));
    command
        .args([
            "--port",
            &port.to_string(),
            "--data-dir",
            &data_dir.to_string_lossy(),
            "--no-open",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .current_dir(&dir);
    // 双保险：即使后端代码没做 reconfigure，也让 Python 以 UTF-8 写标准流
    command.env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8");
    if let Some(skills_dir) = pick_default_skills_dir(app) {
        command.env("GUIDED_READER_DEFAULT_SKILLS_DIR", &skills_dir);
    }
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("启动 Reader 后端失败：{error}"))?;
    #[cfg(target_os = "windows")]
    bind_kill_on_close_job(&child);

    let log = Arc::new(Mutex::new(open_reader_log(&data_dir)));
    let (sender, receiver) = std::sync::mpsc::channel::<String>();
    match child.stdout.take() {
        Some(stdout) => pump_output(stdout, sender.clone(), "out", Arc::clone(&log)),
        None => {
            let _ = child.kill();
            let _ = child.wait();
            return Err("无法读取 Reader 后端输出。".to_string());
        }
    }
    // stderr 也必须排空并纳入诊断：后端真正出错时，原因往往只写在这里
    if let Some(stderr) = child.stderr.take() {
        pump_output(stderr, sender.clone(), "err", Arc::clone(&log));
    }
    drop(sender);

    let deadline = Instant::now() + READY_TIMEOUT;
    let mut ready_url: Option<String> = None;
    let mut failure: Option<String> = None;
    let mut tail: VecDeque<String> = VecDeque::new();
    while Instant::now() < deadline {
        match receiver.recv_timeout(Duration::from_millis(500)) {
            Ok(line) => {
                if let Some(url) = line.strip_prefix("READY ") {
                    ready_url = Some(url.trim().to_string());
                    break;
                }
                let fatal = line.contains("启动失败")
                    || line.contains("Traceback")
                    || line.contains("Error");
                if tail.len() == 8 {
                    tail.pop_front();
                }
                tail.push_back(truncate_line(&line, 200));
                if fatal {
                    failure = Some("后端报告启动错误".to_string());
                    break;
                }
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                // 两条输出流都已关闭：进程确实结束了，取退出码辅助定位
                failure = Some(match child.try_wait() {
                    Ok(Some(status)) => format!("后端进程已退出（{status}）"),
                    Ok(None) => "后端进程输出被关闭".to_string(),
                    Err(error) => format!("后端进程已退出（状态未知：{error}）"),
                });
                break;
            }
        }
    }

    if ready_url.is_none() {
        // 失败必须收尸：残留进程会占住数据目录，让后续重试一路失败
        let _ = child.kill();
        let _ = child.wait();
        state.url.lock().expect("reader url 锁").take();
        let reason = failure.unwrap_or_else(|| "90 秒内未就绪".to_string());
        let detail = tail.iter().cloned().collect::<Vec<_>>().join(" ｜ ");
        let log_hint = data_dir.join("reader-service.log");
        return Err(if detail.is_empty() {
            format!(
                "Reader 后端启动失败：{reason}。详细日志：{}",
                log_hint.display()
            )
        } else {
            format!(
                "Reader 后端启动失败：{reason}。后端输出：{detail}（日志：{}）",
                log_hint.display()
            )
        });
    }
    let url = ready_url.expect("已确认 READY");

    *state.child.lock().expect("reader child 锁") = Some(child);
    *state.url.lock().expect("reader url 锁") = Some(url.clone());
    Ok(url)
}

/// 截断过长的日志行，避免把整段堆栈塞进界面提示
fn truncate_line(text: &str, max_chars: usize) -> String {
    let mut chars = text.chars();
    let head: String = chars.by_ref().take(max_chars).collect();
    if chars.next().is_some() {
        format!("{head}…")
    } else {
        head
    }
}

fn url_port(url: &str) -> Option<u16> {
    let rest = url.split("://").nth(1)?;
    let authority = rest.split('/').next()?;
    authority
        .rsplit(':')
        .next()?
        .split('?')
        .next()?
        .parse()
        .ok()
}

fn open_reader_window(app: &AppHandle, url: &str) -> Result<(), String> {
    let parsed: tauri::Url = url
        .parse()
        .map_err(|error| format!("READY 地址无法解析：{error}"))?;
    if let Some(window) = app.get_webview_window("reader") {
        let _ = window.eval(&format!("location.replace({url:?});"));
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        return Ok(());
    }
    WebviewWindowBuilder::new(app, "reader", WebviewUrl::External(parsed))
        .title("Guided Reader")
        .inner_size(1280.0, 860.0)
        .min_inner_size(960.0, 640.0)
        .build()
        .map_err(|error| format!("打开 Reader 窗口失败：{error}"))?;
    Ok(())
}

/// 前端内嵌入口：确保 Reader 运行并返回 READY URL（不开窗口，iframe 内嵌用）。
#[tauri::command]
fn reader_url(app: AppHandle, state: State<'_, ReaderState>) -> Result<String, String> {
    ensure_reader_running(&app, state.inner())
}

/// 把 READY 地址（`http://127.0.0.1:PORT/?token=…`）换成同一服务上的笔记页地址。
/// 查询串要原样带过去，否则新窗口拿不到 launch token / cookie。
fn notes_url(base: &str) -> String {
    let (root, query) = match base.find('?') {
        Some(at) => (&base[..at], &base[at..]),
        None => (base, ""),
    };
    format!("{}/notes.html{}", root.trim_end_matches('/'), query)
}

fn open_notes_window(app: &AppHandle, url: &str) -> Result<(), String> {
    let target = notes_url(url);
    let parsed: tauri::Url = target
        .parse()
        .map_err(|error| format!("笔记地址无法解析：{error}"))?;
    if let Some(window) = app.get_webview_window("notes") {
        let _ = window.eval(&format!("location.replace({target:?});"));
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        return Ok(());
    }
    // 竖版小窗：默认 520×720，正好放在屏幕一侧与 Reader 并排陪读
    WebviewWindowBuilder::new(app, "notes", WebviewUrl::External(parsed))
        .title("笔记 · Guided Reader")
        .inner_size(520.0, 720.0)
        .min_inner_size(380.0, 480.0)
        .build()
        .map_err(|error| format!("打开笔记窗口失败：{error}"))?;
    Ok(())
}

/// 平台侧栏「笔记」入口：确保 Reader 运行后打开独立的笔记小窗。
#[tauri::command]
fn open_notes(app: AppHandle, state: State<'_, ReaderState>) -> Result<String, String> {
    let url = ensure_reader_running(&app, state.inner())?;
    open_notes_window(&app, &url)?;
    Ok(notes_url(&url))
}

/// 前端入口：确保 Reader 运行，然后在应用内窗口打开（不调用系统浏览器）。
#[tauri::command]
fn open_reader(app: AppHandle, state: State<'_, ReaderState>) -> Result<String, String> {
    let url = ensure_reader_running(&app, state.inner())?;
    open_reader_window(&app, &url)?;
    Ok(url)
}

/// 查询 Reader 当前状态（平台页状态灯用）。
#[tauri::command]
fn reader_status(state: State<'_, ReaderState>) -> Result<serde_json::Value, String> {
    let url = state.url.lock().expect("reader url 锁").clone();
    let running = url
        .as_ref()
        .and_then(|value| url_port(value))
        .map(port_open)
        .unwrap_or(false);
    Ok(serde_json::json!({ "running": running, "url": url }))
}

fn stop_reader(state: &ReaderState) {
    kill_stale(state);
    *state.url.lock().expect("reader url 锁") = None;
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // 第二次启动：唤起已有主窗口
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .manage(ReaderState {
            child: Mutex::new(None),
            url: Mutex::new(None),
            start_lock: Mutex::new(()),
        })
        .invoke_handler(tauri::generate_handler![
            open_reader,
            reader_url,
            reader_status,
            open_notes
        ])
        .on_window_event(|window, event| {
            // 关闭按钮 = 隐藏到托盘，不退出应用；托盘菜单"完全退出"才真正退出。
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .setup(|app| {
            // 托盘图标：左键唤起主窗口，右键菜单可完全退出。
            let show_item = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "完全退出", true, None::<&str>)?;
            let tray_menu = Menu::with_items(app, &[&show_item, &quit_item])?;
            TrayIconBuilder::with_id("main-tray")
                .icon(app.default_window_icon().expect("缺少应用图标").clone())
                .tooltip("SW_Guided_Reader")
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.unminimize();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.unminimize();
                            let _ = window.set_focus();
                        }
                    }
                })
                .build(app)?;

            // 2.0：主窗口本身就是 Reader。启动页（dist/index.html）会调用 reader_url
            // 拿到 READY 地址后 location.replace 过去；这里不再额外开窗口，
            // 也不再用 GUIDED_READER_AUTOSTART 开关（启动即阅读是默认行为）。
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("SW_Guided_Reader 启动失败")
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                let state: State<ReaderState> = app_handle.state();
                stop_reader(state.inner());
            }
        });
}
