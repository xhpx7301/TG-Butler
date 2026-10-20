"use strict";
let csrf = "", state = null;
const $ = id => document.getElementById(id);
const titles = {overview:"运行概览",telegram:"Telegram 账号",notification:"通知机器人",groups:"群组与关注用户",summary:"日报与 AI",checkins:"定时签到",system:"运行设置",admin:"管理员账户"};
const pageSummaries = {overview:"每分钟查看监控、通知、日报和最近执行记录。",telegram:"连接个人 Telegram 账号，读取群组消息并执行任务。",notification:"管理提醒机器人和接收通知的目标。",groups:"选择监控范围，查询群组与成员数字 ID。",summary:"配置每日摘要、模型服务和测试发送。",checkins:"设置每日命令、回复规则和签到时间。",system:"调整时区、消息留存和通知队列。",admin:"管理网页后台的登录账号与安全会话。"};
let toastTimer;
function toast(message, error=false) { $("toast").textContent=message; $("toast").className=error?"error":""; $("toast").hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$("toast").hidden=true,9000); }
async function api(path, data) {
  const response=await fetch("/api/"+path,{method:data===undefined?"GET":"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:data===undefined?undefined:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok){ if(response.status===401 && path!=="login") showLogin(); throw Error(result.error||"请求失败"); }
  return result;
}
function showLogin(){ setMenuOpen(false,false);$("login").hidden=false;$("dashboard").hidden=true; }
function fields(form){ return Object.fromEntries(new FormData(form)); }
function syncStateToggle(input){const key=input.name||input.dataset.key;const button=input.parentElement.querySelector(`[data-toggle-for="${key}"]`);if(!button)return;const active=input.checked;button.setAttribute("aria-checked",String(active));button.classList.toggle("is-active",active);button.textContent=active?"● 已启用":"○ 已停用";}
function addStateToggle(input){
  const button=document.createElement("button");button.type="button";button.className="state-toggle";button.dataset.toggleFor=input.name||input.dataset.key;button.setAttribute("role","switch");button.setAttribute("aria-label",input.closest("label").textContent.replace(/● 已启用|○ 已停用/g,"").trim());
  input.insertAdjacentElement("afterend",button);input.classList.add("state-input");
  button.addEventListener("click",()=>{input.checked=!input.checked;syncStateToggle(input);input.dispatchEvent(new Event("change",{bubbles:true}));});syncStateToggle(input);return button;
}
function fill(id, data){ const form=$(id); for(const [key,value] of Object.entries(data)){const field=form.elements.namedItem(key);if(!field)continue;if(field.type==="checkbox"){field.checked=!!value;syncStateToggle(field);}else field.value=value??"";} }
function bindForm(id, handler){ $(id).addEventListener("submit",async event=>{event.preventDefault();const buttons=[...event.target.querySelectorAll("button")];buttons.forEach(b=>b.disabled=true);try{await handler(fields(event.target),event.target);}catch(error){toast(error.message,true);}finally{buttons.forEach(b=>b.disabled=false);}}); }
function bindButton(id, handler){ $(id).addEventListener("click",async()=>{const button=$(id);button.disabled=true;try{await handler();}catch(error){toast(error.message,true);}finally{button.disabled=false;}}); }
const mobileNavigation=window.matchMedia("(max-width:680px)");
function setMenuOpen(open,restoreFocus=true){
  const sidebar=$("sidebar"),mobile=mobileNavigation.matches;
  open=mobile && open;
  const wasOpen=document.body.classList.contains("menu-open");
  document.body.classList.toggle("menu-open",open);
  $("menu-toggle").setAttribute("aria-expanded",String(open));
  $("nav-backdrop").hidden=!open;
  $("main-content").inert=open;
  sidebar.inert=mobile && !open;
  if(mobile)sidebar.setAttribute("aria-hidden",String(!open));else sidebar.removeAttribute("aria-hidden");
  if(open){sidebar.setAttribute("role","dialog");sidebar.setAttribute("aria-modal","true");$("menu-close").focus();}
  else{sidebar.removeAttribute("role");sidebar.removeAttribute("aria-modal");if(wasOpen && restoreFocus && mobile)$("menu-toggle").focus();}
}
$("menu-toggle").onclick=()=>setMenuOpen(true);
$("menu-close").onclick=()=>setMenuOpen(false);
$("nav-backdrop").onclick=()=>setMenuOpen(false);
mobileNavigation.addEventListener("change",()=>setMenuOpen(false,false));
document.addEventListener("keydown",event=>{
  if(!document.body.classList.contains("menu-open"))return;
  if(event.key==="Escape"){event.preventDefault();setMenuOpen(false);}
  if(event.key==="Tab"){
    const controls=[...$("sidebar").querySelectorAll("a[href],button:not(:disabled)")].filter(el=>el.getClientRects().length);
    const first=controls[0],last=controls[controls.length-1];
    if(event.shiftKey && document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}
  }
});
setMenuOpen(false,false);
function navigate(page){document.querySelectorAll("[data-section]").forEach(s=>s.hidden=s.dataset.section!==page);document.querySelectorAll("[data-page]").forEach(b=>{b.classList.toggle("selected",b.dataset.page===page);if(b.closest("nav")){if(b.dataset.page===page)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");}});$("page-title").textContent=titles[page];$("page-summary").textContent=pageSummaries[page]||"";$("global-notice").hidden=page!=="overview";setMenuOpen(false);if(mobileNavigation.matches)window.scrollTo({top:0,behavior:"instant"});}
async function load(){
  state=await api("state"); $("login").hidden=true;$("dashboard").hidden=false;
  const config=state.config, summary=config.summary||{};
  const serviceActive=state.service==="active";$("service-badge").textContent=serviceActive?"任务运行中":"任务未运行";$("service-badge").className="badge"+(serviceActive?"":" off");$("service-state-card").textContent=serviceActive?"运行中":"未运行";$("service-panel-dot").textContent=serviceActive?"在线":"离线";$("service-panel-dot").className="status-dot"+(serviceActive?"":" off");
  $("count-groups").textContent=(config.groups||[]).length;$("count-checkins").textContent=(config.checkins||[]).length;
  $("summary-time").textContent=summary.enabled===false?"已关闭":summary.at||"00:05";$("timezone-label").textContent=config.timezone||"Asia/Shanghai";
  fill("application-form",{TG_API_ID:state.credentials.TG_API_ID||"",TG_API_HASH:""});
  fill("monitor-notification-form",{TG_MONITOR_NOTIFY_CHAT_ID:state.credentials.TG_MONITOR_NOTIFY_CHAT_ID||state.credentials.TG_NOTIFY_CHAT_ID||"",TG_MONITOR_BOT_TOKEN:""});
  fill("report-notification-form",{TG_REPORT_NOTIFY_CHAT_ID:state.credentials.TG_REPORT_NOTIFY_CHAT_ID||state.credentials.TG_NOTIFY_CHAT_ID||"",TG_REPORT_BOT_TOKEN:""});
  for(const [form,key] of [["application-form","TG_API_HASH"],["monitor-notification-form","TG_MONITOR_BOT_TOKEN"],["report-notification-form","TG_REPORT_BOT_TOKEN"],["ai-form","SUMMARY_API_KEY"]])$(form).elements.namedItem(key).placeholder=state.configured[key]?"已保存 · 留空保留":"尚未设置";
  fill("summary-form",{enabled:true,at:"00:05",max_messages:500,max_chars:30000,...summary});
  setModelOptions([], summary.model||"");
  fill("system-form",{timezone:config.timezone||"Asia/Shanghai",retention_days:config.retention_days||30,queue_size:(config.notifications||{}).queue_size||500});
  $("group-list").replaceChildren();(config.groups||[]).forEach(addGroup);
  $("checkin-list").replaceChildren();(config.checkins||[]).forEach(addCheckin);
  $("runs").replaceChildren();for(const row of state.runs){const tr=document.createElement("tr");for(const value of [row.day,row.kind,row.name,row.status]){const td=document.createElement("td");td.textContent=value;tr.append(td);}$("runs").append(tr);}
}
function inputField(container,key,label,value,help,type="text"){
  const wrapper=document.createElement("label"), input=document.createElement("input"), small=document.createElement("small");wrapper.append(document.createTextNode(label));input.dataset.key=key;input.type=type;if(type==="checkbox"){wrapper.className="check";input.checked=!!value;}else input.value=value??"";wrapper.append(input);small.textContent=help;wrapper.append(small);container.append(wrapper);if(type==="checkbox")addStateToggle(input);return input;
}
function record(container,title,original){const box=document.createElement("div");box.className="record";box._original=structuredClone(original);const heading=document.createElement("div");heading.className="record-heading";const name=document.createElement("b");name.textContent=title;const remove=document.createElement("button");remove.type="button";remove.className="danger";remove.textContent="删除";remove.onclick=()=>{if(confirm("删除此项？点击保存后生效。"))box.remove();};heading.append(name,remove);const grid=document.createElement("div");grid.className="grid";box.append(heading,grid);$(container).append(box);return grid;}
function addGroup(group={}){const grid=record("group-list",group.name||"监控群组",group);inputField(grid,"id","群组 ID",group.id,"在 Telegram 账号页读取，保留 -100 等负号。");inputField(grid,"name","备注名称",group.name,"自定义，用于提醒中区分群组。");inputField(grid,"watch_users","关注用户 ID",(group.watch_users||[]).join(","),"数字 ID 用英文或中文逗号分隔，在成员列表查询。留空不指定关注用户。");inputField(grid,"notify_all","全群消息提醒",group.notify_all,"打开后每条收到的新消息都提醒。","checkbox");inputField(grid,"daily_summary","参与每日日报",group.daily_summary!==false,"由关注-日报机器人发送。","checkbox");inputField(grid,"report_keywords","自定义提醒关键词",(group.report_keywords||[]).join(","),"自定义机器人匹配关注用户消息；英文或中文逗号分隔，留空关闭关键词筛选。");inputField(grid,"report_match_mode","关键词匹配方式",group.report_match_mode||"any","any=命中任一关键词，all=必须同时命中。");inputField(grid,"report_images","自定义提醒图片",group.report_images,"关注用户发送图片时通知自定义机器人并附原消息。","checkbox");inputField(grid,"report_links","自定义提醒链接",group.report_links,"关注用户发送链接时通知自定义机器人并附原消息。","checkbox");const test=document.createElement("button");test.type="button";test.className="secondary group-test";test.textContent="测试通知与模板";test.title="向通知机器人发送模拟群消息，检查两种通知模板";test.onclick=async()=>{let payload;try{payload=groupValues(grid.parentElement);}catch(error){toast(error.message,true);return;}if(!confirm(`将向已配置的通知机器人发送“${payload.name||payload.id}”群组的模拟消息，用于检查通知与模板。继续吗？`))return;test.disabled=true;try{toast((await api("group-test",payload)).message);}catch(error){toast(error.message,true);}finally{test.disabled=false;}};grid.parentElement.append(test);}
function addCheckin(job={}){const grid=record("checkin-list",job.name||"签到任务",job);for(const [key,label,value,help,type] of [
  ["name","任务唯一名称",job.name,"同一名称每天只尝试一次；修改名称视为新任务。"],
  ["chat_id","签到群组 ID",job.chat_id,"在 Telegram 账号页查询群组列表。"],
  ["at","每日时间",job.at||"09:00","使用运行设置中的时区。","time"],
  ["command","签到命令",job.command||"/checkin","查看目标群内签到机器人的帮助，填写其接受的命令。"],
  ["responder_id","回复机器人 ID（可选）",job.responder_id,"从成员列表获取。与成功规则同时填写；留空仅通知已发送。"],
  ["success_pattern","成功回复正则（可选）",job.success_pattern,"根据实际回复填写，例如 签到成功|已签到。"],
  ["failure_pattern","失败回复正则（可选）",job.failure_pattern,"例如 签到失败|无权限；留空不匹配失败回复。"],
  ["timeout_seconds","等待回复秒数",job.timeout_seconds||60,"1–600；超时会标记结果未确认。","number"],
  ["require_reply","仅匹配回复本次命令",job.require_reply!==false,"推荐打开，避免把其他人的成功回复算给你。","checkbox"]])inputField(grid,key,label,value,help,type||"text");const test=document.createElement("button");test.type="button";test.className="secondary";test.textContent="测试签到";test.title="真实发送此任务的签到命令，不占用当天正式签到次数";test.onclick=async()=>{const name=grid.querySelector('[data-key="name"]').value.trim();if(!name){toast("请填写任务名称",true);return;}if(!confirm(`将使用已保存的“${name}”任务向目标群真实发送签到命令，并等待回复；不会占用当天正式签到次数。继续吗？`))return;test.disabled=true;try{toast((await api("checkin-test",{name})).message);}catch(error){toast(error.message,true);}finally{test.disabled=false;}};grid.parentElement.append(test);}
function records(id){return [...$(id).querySelectorAll(".record")].map(box=>{const values={...box._original};box.querySelectorAll("[data-key]").forEach(input=>values[input.dataset.key]=input.type==="checkbox"?input.checked:input.value.trim());return values;});}
function integer(value,label){if(!/^-?\d+$/.test(String(value)))throw Error(label+"需要填写数字");const result=Number(value);if(!Number.isSafeInteger(result))throw Error(label+"超出支持范围");return result;}
function setModelOptions(models, selected=""){
  const select=$("summary-model-select");select.replaceChildren();
  const placeholder=document.createElement("option");placeholder.value="";placeholder.textContent=models.length?"请选择模型":"请先读取模型";select.append(placeholder);
  for(const model of models){const option=document.createElement("option");option.value=model;option.textContent=model;select.append(option);}
  if(selected && !models.includes(selected)){const option=document.createElement("option");option.value=selected;option.textContent=selected+"（当前配置）";select.append(option);}
  select.value=selected||"";
}
function aiFormValues(){return {api_url:$("summary-form").elements.api_url.value.trim(),model:$("summary-model-manual").value.trim()||$("summary-model-select").value,api_key:$("ai-form").elements.SUMMARY_API_KEY.value.trim()};}
function showModels(models){const current=$("summary-model-manual").value.trim();setModelOptions(models,current);if(!models.length)toast("接口返回的模型列表为空",true);else toast(`已读取 ${models.length} 个模型，请在下拉框中选择`);}
async function saveConfig(change){const config=structuredClone(state.config);change(config);const result=await api("config",{config,revision:state.revision});await load();toast(result.message);}
for(const input of document.querySelectorAll('#summary-form input[type="checkbox"]'))addStateToggle(input);
for(const button of document.querySelectorAll("[data-page]"))button.onclick=()=>navigate(button.dataset.page);
bindForm("login-form",async(data,form)=>{const result=await api("login",data);csrf=result.csrf;$("logged-user").textContent=result.username;fill("admin-form",{username:result.username});form.reset();await load();});
bindButton("logout",async()=>{await api("logout",{});location.reload();});
bindButton("refresh",async()=>{await load();toast("状态已刷新");});
for(const [id,section] of [["application-form","application"],["monitor-notification-form","monitor"],["report-notification-form","report"],["ai-form","ai"]])bindForm(id,async(data,form)=>{if(section==="ai")data.clear_key=form.elements.clear_key.checked;const result=await api("credentials/"+section,data);form.querySelectorAll("input[type=password]").forEach(input=>input.value="");await load();toast(result.message);});
for(const [id,action] of [["send-code-form","send-code"],["verify-code-form","verify-code"],["password-form","password"],["members-form","members"],["history-form","history"],["group-resolve-form","resolve"],["user-resolve-form","resolve"]])bindForm(id,async(data,form)=>{const result=await api("telegram/"+action,data);if(action==="members")showItems(result.items);else if(action==="resolve"){showItems([{id:result.id,name:result.name,username:result.username}]);toast(`${result.kind==="group"?"群组":"用户"} ID：${result.id}`);}else if(action==="history"){$("history-status").textContent=result.message;toast(result.message);}else{$("telegram-status").textContent=result.message;$("password-form").hidden=result.status!=="password";if(action!=="send-code")form.reset();toast(result.message);}});
function showItems(items){$("telegram-items").hidden=false;$("telegram-items").textContent=items.map(row=>`${row.id}  ${row.name}  ${row.username?"@"+row.username:""}`).join("\n")||"没有可显示的记录";}
bindButton("dialogs",async()=>showItems((await api("telegram/dialogs",{})).items));
bindButton("monitor-notify-test",async()=>toast((await api("notify-test",{role:"monitor"})).message));
bindButton("report-notify-test",async()=>toast((await api("notify-test",{role:"report"})).message));
bindButton("fetch-models",async()=>showModels((await api("ai/models",aiFormValues())).models));
bindButton("test-ai",async()=>toast((await api("ai/test",aiFormValues())).message));
bindButton("test-summary",async()=>{if(!confirm("将发送前一天的测试日报到通知机器人，且不会记录为正式日报。继续吗？"))return;toast((await api("summary-test",{})).message);});
bindButton("update-project",async()=>{const result=await api("update",{});$("service-output").hidden=false;$("service-output").textContent=result.message;toast("更新已开始，页面将在 10 秒后自动刷新。");window.setTimeout(()=>window.location.reload(),10000);});
$("summary-model-select").addEventListener("change",event=>$("summary-model-manual").value=event.target.value);
bindButton("add-group",()=>addGroup());bindButton("add-checkin",()=>addCheckin());
function groupValues(box){const row={...box._original};box.querySelectorAll("[data-key]").forEach(input=>row[input.dataset.key]=input.type==="checkbox"?input.checked:input.value.trim());return {...row,id:integer(row.id,"群组 ID"),watch_users:row.watch_users.replaceAll("，",",").split(",").filter(x=>x.trim()).map(x=>integer(x.trim(),"关注用户 ID")),report_keywords:row.report_keywords.replaceAll("，",",").split(",").map(x=>x.trim()).filter(Boolean),report_match_mode:row.report_match_mode.trim().toLowerCase()||"any",report_images:row.report_images,report_links:row.report_links};}
bindButton("save-groups",()=>saveConfig(config=>config.groups=[...$("group-list").querySelectorAll(".record")].map(groupValues)));
bindButton("save-checkins",()=>saveConfig(config=>config.checkins=records("checkin-list").map(row=>({...row,chat_id:integer(row.chat_id,"群组 ID"),responder_id:row.responder_id?integer(row.responder_id,"机器人 ID"):null,success_pattern:row.success_pattern||null,failure_pattern:row.failure_pattern||null,timeout_seconds:integer(row.timeout_seconds,"超时秒数")}))));
bindForm("summary-form",(data,form)=>saveConfig(config=>config.summary={...config.summary,...data,enabled:form.elements.enabled.checked,api_url:data.api_url||null,model:data.model||null,max_messages:integer(data.max_messages,"消息数"),max_chars:integer(data.max_chars,"字符数")}));
bindForm("system-form",data=>saveConfig(config=>{config.timezone=data.timezone;config.retention_days=integer(data.retention_days,"保留天数");config.notifications={...config.notifications,queue_size:integer(data.queue_size,"队列容量")};}));
bindForm("admin-form",async(data,form)=>{if(data.password!==data.confirm_password)throw Error("两次新密码不一致");const result=await api("admin",data);form.reset();location.reload();toast(result.message);});
for(const button of document.querySelectorAll("[data-service]"))button.onclick=async()=>{button.disabled=true;try{if(button.dataset.service==="stop"&&!confirm("确认停止群消息监控和定时任务？"))return;const result=await api("service/"+button.dataset.service,{});$("service-output").hidden=false;$("service-output").textContent=result.message;if(button.dataset.service!=="logs")await load();}catch(error){toast(error.message,true);}finally{button.disabled=false;}};
(async()=>{try{const info=await api("session");csrf=info.csrf;if(info.authenticated){$("logged-user").textContent=info.username;fill("admin-form",{username:info.username});await load();}else showLogin();}catch(error){toast(error.message,true);}})();
