// Read-only status adapter for the installed ChatGPT desktop app.
// No input injection, browser debugging, credentials or network access.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Automation;

public static class ChatGPTProbe {
    static TreeWalker Walker = TreeWalker.ControlViewWalker;
    static string Name(AutomationElement e) { try { return e.Current.Name ?? ""; } catch { return ""; } }
    static ControlType Type(AutomationElement e) { try { return e.Current.ControlType; } catch { return null; } }
    static Condition IsType(ControlType t) { return new PropertyCondition(AutomationElement.ControlTypeProperty, t); }
    static AutomationElement ByName(AutomationElement e, string name) {
        return e.FindFirst(TreeScope.Descendants, new PropertyCondition(AutomationElement.NameProperty, name));
    }
    static string State(string s) {
        s = s.ToLowerInvariant();
        if (s.Contains("失败") || s.Contains("failed")) return "failed";
        if (s.Contains("等待") || s.Contains("需要批准") || s.Contains("需要注意") ||
            s.Contains("waiting") || s.Contains("needs attention") || s.Contains("approval")) return "waiting";
        if (s.Contains("处理中") || s.Contains("进行中") || s.Contains("思考中") ||
            s.Contains("working") || s.Contains("thinking") || s.Contains("in progress")) return "running";
        return "";
    }
    static bool IsUserHeading(string s) {
        return s == "你说：" || s == "你说:" || s == "You said:" || s == "You said";
    }
    static bool IsAssistantHeading(string s) {
        return s == "ChatGPT 说：" || s == "ChatGPT 说:" || s == "ChatGPT said:" ||
            s == "Codex said:" || s == "Codex 说：";
    }
    static void UserText(AutomationElement e, StringBuilder b, int depth) {
        if (e == null || depth > 12 || b.Length >= 8192) return;
        ControlType t = Type(e);
        string n = Name(e);
        if (t == ControlType.Button || t == ControlType.Image || t == ControlType.Edit) return;
        if ((t == ControlType.Text || t == ControlType.Hyperlink) && n.Length > 0) {
            b.Append(n); b.Append("\n"); return;
        }
        for (var c = Walker.GetFirstChild(e); c != null; c = Walker.GetNextSibling(c)) UserText(c, b, depth+1);
    }
    static string Prompt(AutomationElement region) {
        var headings = region.FindAll(TreeScope.Descendants, new OrCondition(
            new PropertyCondition(AutomationElement.NameProperty, "你说："),
            new PropertyCondition(AutomationElement.NameProperty, "你说:"),
            new PropertyCondition(AutomationElement.NameProperty, "You said:"),
            new PropertyCondition(AutomationElement.NameProperty, "You said")));
        if (headings.Count == 0) return "";
        var messages = new List<string>();
        for (int i=Math.Max(0, headings.Count-16); i<headings.Count; i++)
            messages.Add(ExtractAfter(headings[i]));
        return SelectPrompt(messages);
    }
    static bool IsAck(string s) {
        s = (s ?? "").Trim();
        if (Regex.IsMatch(s, @"^(已经?切换|已完成|已恢复|已打开|已关闭|已确认)", RegexOptions.IgnoreCase) &&
            s.Length < 200 && !Regex.IsMatch(s, @"请|帮我|需要|要求|[?？]")) return true;
        return Regex.IsMatch(s, @"^(已切换|已完成|已恢复|已打开|已关闭|已确认|可以|允许|同意|好|好的|继续|完成|是|否|yes|no|ok|done|ready)[。！.!]*$", RegexOptions.IgnoreCase);
    }
    public static string SelectPrompt(List<string> messages) {
        for (int i=messages.Count-1; i>=0; i--) {
            string value=(messages[i] ?? "").Trim();
            if (value.Length==0 || IsAck(value)) continue;
            string[] parts=value.Split(new char[]{'\n'}, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length>1 && IsAck(parts[parts.Length-1]) &&
                Regex.IsMatch(parts[0], @"是否|回复|我会|请切换")) continue;
            // Inline question cards can repeat an acknowledgement as another user bubble.
            // Do not mistake the assistant's clarification question for the user's task.
            if (i>0 && IsAck(messages[i-1]) && value.EndsWith(messages[i-1].Trim(), StringComparison.Ordinal) &&
                value.Length>messages[i-1].Trim().Length) continue;
            return value;
        }
        return "";
    }
    static string ExtractAfter(AutomationElement last) {
        var b = new StringBuilder();
        int budget = 100;
        for (var c = Walker.GetNextSibling(last); c != null && budget-- > 0; c = Walker.GetNextSibling(c)) {
            string n = Name(c);
            if (IsAssistantHeading(n) || IsUserHeading(n) || n == "最新一条回复" || n == "Latest reply") break;
            if (Type(c) == ControlType.Button && (n.Contains("复制") || n.Contains("Copy"))) break;
            if (Regex.IsMatch(n, @"^\d{1,2}:\d{2}$")) continue;
            UserText(c, b, 0);
        }
        return b.ToString().Trim();
    }
    static object Read() {
        var rows = new List<object>();
        var scopes = new List<object>();
        var currents = new List<object>();
        int windows = 0;
        foreach (var p in Process.GetProcessesByName("ChatGPT")) {
            try {
                IntPtr handle = p.MainWindowHandle;
                if (handle == IntPtr.Zero) continue;
                var root = AutomationElement.FromHandle(handle);
                var doc = root.FindFirst(TreeScope.Descendants, IsType(ControlType.Document));
                if (doc == null) continue;
                windows++;
                string window = handle.ToInt64().ToString();
                var buttons = doc.FindAll(TreeScope.Descendants, IsType(ControlType.Button));
                string sideSource = "";
                foreach (AutomationElement b in buttons) {
                    string n = Name(b);
                    if (n.StartsWith("切换模式") || n.StartsWith("Switch mode")) {
                        sideSource = n.IndexOf("Codex", StringComparison.OrdinalIgnoreCase) >= 0 ? "codex" :
                            n.IndexOf("ChatGPT", StringComparison.OrdinalIgnoreCase) >= 0 ? "chatgpt" : "";
                        break;
                    }
                }
                var visible = new List<string>();
                if (sideSource.Length > 0) {
                    // A sidebar chat is a button with pin/archive children; no body traversal.
                    var seen = new HashSet<string>();
                    foreach (AutomationElement b in buttons) {
                        string n = Name(b);
                        if (String.IsNullOrWhiteSpace(n) || !seen.Add(n)) continue;
                        var nested = b.FindAll(TreeScope.Descendants, IsType(ControlType.Button));
                        bool isChat = false;
                        foreach (AutomationElement child in nested) {
                            string cn = Name(child);
                            if (cn == "归档聊天" || cn == "Archive chat" || cn == "置顶聊天" || cn == "Pin chat") { isChat = true; break; }
                        }
                        if (!isChat) continue;
                        visible.Add(n);
                        string state = "";
                        foreach (AutomationElement status in b.FindAll(TreeScope.Descendants, IsType(ControlType.StatusBar))) {
                            string candidate = State(Name(status));
                            if (candidate.Length > 0) state = candidate;
                        }
                        if (state.Length > 0)
                            rows.Add(new { source=sideSource, title=n, state=state, window=window });
                    }
                    scopes.Add(new { source=sideSource, window=window, titles=visible });
                }
                AutomationElement region = ByName(doc, "对话") ?? ByName(doc, "Conversation");
                if (region == null) continue;
                string currentSource = "";
                foreach (AutomationElement edit in region.FindAll(TreeScope.Descendants, IsType(ControlType.Edit))) {
                    string en = Name(edit);
                    if (en.IndexOf("Codex", StringComparison.OrdinalIgnoreCase) >= 0) currentSource = "codex";
                    else if (en.IndexOf("ChatGPT", StringComparison.OrdinalIgnoreCase) >= 0 || en == "Ask anything" || en == "询问任何问题")
                        currentSource = "chatgpt";
                }
                if (currentSource.Length == 0) continue; // Never guess from the sidebar's mode.
                bool running = false;
                foreach (AutomationElement b in region.FindAll(TreeScope.Descendants, IsType(ControlType.Button))) {
                    string n = Name(b);
                    if (n == "停止" || n == "停止生成" || n == "Stop" || n == "Stop generating" || n == "Stop streaming")
                        running = true;
                }
                string title = Name(doc);
                string prompt = Prompt(region);
                currents.Add(new { source=currentSource, title=title, prompt=prompt, state=running?"running":"idle", window=window });
            } catch (ElementNotAvailableException) { }
              catch (Exception) { }
            finally { p.Dispose(); }
        }
        return new { ok=windows>0, windows=windows, tasks=rows, scopes=scopes, current=currents };
    }
    [MTAThread]
    public static void Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        if (args.Length>0 && args[0]=="--self-test") {
            if (SelectPrompt(new List<string>{"原始任务：核对广告报表","已切换","请切换模式后回复已切换。\n已切换"}) != "原始任务：核对广告报表")
                throw new Exception("question card acknowledgement");
            if (SelectPrompt(new List<string>{"原始任务：核对广告报表","还要保留当前问题"}) != "还要保留当前问题")
                throw new Exception("substantive clarification");
            if (SelectPrompt(new List<string>{"帮我解释API"}) != "帮我解释API")
                throw new Exception("short task");
            if (SelectPrompt(new List<string>{"原始任务：修复读取","ChatGPT 验证已通过，请切换后回复，我会核对字段。\n已切换到工作codex，当前对话就是正在执行的codex对话"}) != "原始任务：修复读取")
                throw new Exception("question card long acknowledgement");
            if (SelectPrompt(new List<string>{"原始任务","已切换到Codex，请帮我修复另外的问题"}) != "已切换到Codex，请帮我修复另外的问题")
                throw new Exception("new task after acknowledgement");
            Console.WriteLine("PASS 5 prompt selection regressions"); return;
        }
        var json = new JavaScriptSerializer();
        bool once = args.Length > 0 && args[0] == "--once";
        do {
            try { Console.WriteLine(json.Serialize(Read())); }
            catch (Exception e) { Console.WriteLine(json.Serialize(new {ok=false,error=e.GetType().Name})); }
            Console.Out.Flush();
            if (once) return;
            Thread.Sleep(2000);
        } while(true);
    }
}
