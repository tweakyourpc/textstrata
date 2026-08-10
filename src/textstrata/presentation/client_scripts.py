"""Browser behavior script bundles for server-rendered pages.

These functions keep JavaScript ownership outside the HTML page renderers while
preserving the current inline-script delivery model.
"""

from __future__ import annotations

from html import escape

from .library_client import library_page_script


def item_page_script(item_id: str) -> str:
    return f"""  <script>
  (()=>{{
    const $=s=>document.querySelector(s);
    const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));
    async function api(url,o={{}}){{const r=await fetch(url,o);let d={{}};try{{d=await r.json()}}catch{{d={{error:await r.text()}}}}if(!r.ok){{const e=new Error(d.error||d.detail||`Request failed (${{r.status}})`);e.data=d;throw e}}return d}}

    function toast(msg, type, duration) {{
      type = type || "info"; duration = duration || 3000;
      const c = document.getElementById("toast-container");
      if (!c) return;
      const icons = {{success:"\\u2713",error:"\\u2717",info:"\\u2139"}};
      const el = document.createElement("div");
      el.className = "toast toast-" + type;
      el.innerHTML = '<span class="toast-icon">' + (icons[type] || "") + '</span><span>' + esc(msg) + '</span>';
      c.appendChild(el);
      setTimeout(() => {{ el.style.animation = "toast-out 180ms ease forwards"; setTimeout(() => el.remove(), 180); }}, duration);
    }}

    document.addEventListener("click", (event) => {{
      const image = event.target.closest("[data-lightbox]");
      if (!image) return;
      const dialog = document.createElement("dialog");
      dialog.className = "textstrata-lightbox";
      dialog.innerHTML = `<form method="dialog"><button aria-label="Close image">Close</button></form><img src="${{image.currentSrc || image.src}}" alt="${{esc(image.alt)}}">`;
      document.body.appendChild(dialog);
      dialog.addEventListener("close", () => dialog.remove(), {{once:true}});
      dialog.showModal();
    }});

    let linkTargets=[]; let wikiMatches=[]; let wikiIndex=-1;
    function targetFor(value) {{
      const needle=String(value||"").trim().toLowerCase();
      return linkTargets.find(t=>[t.id,t.title,...(t.aliases||[])].some(v=>String(v).toLowerCase()===needle));
    }}
    function renderWikiLinks(html) {{
      return html.replace(/\\[\\[([^\\]|#]+)(?:#[^\\]|]+)?(?:\\|([^\\]]+))?\\]\\]/g,(full,target,label)=>{{
        const found=targetFor(target); const text=label||found?.title||target;
        return found ? `<a class="wikilink" href="/item/${{encodeURIComponent(found.id)}}">${{esc(text)}}</a>` : `<span class="wikilink-missing">${{esc(full)}}</span>`;
      }});
    }}
    function renderPreview(md) {{
      let h = md
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/^### (.+)$/gm, "<h3>$1</h3>")
        .replace(/^## (.+)$/gm, "<h2>$1</h2>")
        .replace(/^# (.+)$/gm, "<h1>$1</h1>")
        .replace(/```[\\s\\S]*?```/g, function(m){{return "<pre><code>" + esc(m.replace(/```\\w*\\n?/,"").replace(/```$/,"")) + "</code></pre>"}})
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>")
        .replace(/__([^_]+)__/g, "<strong>$1</strong>")
        .replace(/\\*([^*]+)\\*/g, "<em>$1</em>")
        .replace(/_([^_]+)_/g, "<em>$1</em>")
        .replace(/!\\[([^\\]]*)\\]\\(([^)]+)\\)/g, '<figure><img src="$2" alt="$1" loading="lazy" style="max-width:100%"><figcaption style="font-size:.82rem;color:var(--muted);font-style:italic">$1</figcaption></figure>')
        .replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '<a href="$2">$1</a>');
      h = renderWikiLinks(h);
      h = h.split("\\n\\n").map(function(b){{return b.trim() ? "<p>" + b.replace(/\\n/g, "<br>") + "</p>" : ""}}).join("\\n");
      var ul = h.match(/(<li>[\\s\\S]*?<\\/li>)/g);
      if (ul) h = h.replace(ul[0], function(){{return "<ul>" + ul[0] + "</ul>"}});
      return h || "<p class=\\"meta\\" style=\\"text-align:center\\">Preview appears here as you type.</p>";
    }}

    function updatePreview() {{
      const pv = document.getElementById("live-preview");
      if (pv) pv.innerHTML = renderPreview(ta.value);
    }}

    const editBtn=$("#edit-btn"),saveBtn=$("#save-btn"),cancelBtn=$("#cancel-btn"),ta=$("#edit-textarea"),editStatus=$("#edit-status"),copyMdBtn=$("#copy-md-btn"),focusBtn=$("#focus-btn"),renameBtn=$("#rename-btn");
    editBtn.onclick=()=>{{document.body.classList.toggle("editing");if(document.body.classList.contains("editing")){{ta.focus();ta.setSelectionRange(0,0);loadLinkTargets().then(updatePreview)}}}};
    cancelBtn.onclick=()=>document.body.classList.remove("editing");
    if (focusBtn) focusBtn.onclick = () => document.body.classList.toggle("focus-mode");
    renameBtn?.addEventListener("click",async()=>{{const next=window.prompt("New note ID (lowercase letters, numbers, dots, underscores, or hyphens):","{escape(item_id, quote=True)}");if(!next||next==="{escape(item_id, quote=True)}")return;try{{const result=await api("/api/textstrata/item/{escape(item_id, quote=True)}/rename",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{new_id:next.trim()}})}});toast("Renamed and updated "+result.updated_references+" references","success");setTimeout(()=>location.href="/item/"+encodeURIComponent(result.item_id),400)}}catch(error){{toast(error.message||"Rename failed","error")}}}});

    if(copyMdBtn)copyMdBtn.onclick=async()=>{{try{{await navigator.clipboard.writeText(ta.value);copyMdBtn.textContent="Copied!";toast("Markdown copied to clipboard","success");setTimeout(()=>copyMdBtn.textContent="Copy markdown",1500)}}catch{{toast("Clipboard unavailable","error")}}}};

    async function loadLinkTargets() {{
      if (linkTargets.length) return;
      try {{ linkTargets=(await api("/api/textstrata/link-targets")).targets||[]; }} catch {{ linkTargets=[]; }}
    }}
    function activeWikiQuery() {{
      const before=ta.value.slice(0,ta.selectionStart); const start=before.lastIndexOf("[[");
      if (start<0 || before.slice(start).includes("]]")) return null;
      return {{start, query:before.slice(start+2)}};
    }}
    function closeWikiSuggestions() {{ wikiSuggestions?.classList.remove("open"); wikiMatches=[]; wikiIndex=-1; }}
    function renderWikiSuggestions() {{
      if (!wikiSuggestions) return;
      const query=activeWikiQuery();
      if (!query) {{ closeWikiSuggestions(); return; }}
      const needle=query.query.toLowerCase();
      wikiMatches=linkTargets.filter(t=>[t.id,t.title,...(t.aliases||[])].some(v=>String(v).toLowerCase().includes(needle))).slice(0,8);
      if (!wikiMatches.length) {{ closeWikiSuggestions(); return; }}
      wikiSuggestions.innerHTML=wikiMatches.map((t,i)=>`<button type="button" class="wiki-suggestion" role="option" aria-selected="${{i===wikiIndex}}" data-wiki-index="${{i}}"><strong>${{esc(t.title)}}</strong> <small>${{esc(t.id)}}</small>${{t.aliases?.length?`<span class="meta">${{esc(t.aliases.join(", "))}}</span>`:""}}</button>`).join("");
      wikiSuggestions.classList.add("open");
    }}
    function insertWikiTarget(index) {{
      const query=activeWikiQuery(), target=wikiMatches[index]; if(!query||!target)return;
      const end=ta.selectionStart; ta.setRangeText(`[[${{target.id}}]]`,query.start,end,"end"); ta.focus(); closeWikiSuggestions(); updatePreview();
    }}
    const wikiSuggestions=$("#wiki-suggestions");
    ta.addEventListener("input",()=>{{loadLinkTargets().then(renderWikiSuggestions)}});
    ta.addEventListener("keydown",e=>{{if(!wikiSuggestions?.classList.contains("open"))return;if(e.key==="ArrowDown"||e.key==="ArrowUp"){{e.preventDefault();wikiIndex=(wikiIndex+(e.key==="ArrowDown"?1:-1)+wikiMatches.length)%wikiMatches.length;renderWikiSuggestions()}}else if(e.key==="Enter"||e.key==="Tab"){{e.preventDefault();insertWikiTarget(wikiIndex<0?0:wikiIndex)}}else if(e.key==="Escape")closeWikiSuggestions()}});
    wikiSuggestions?.addEventListener("mousedown",e=>{{const button=e.target.closest("[data-wiki-index]");if(button){{e.preventDefault();insertWikiTarget(Number(button.dataset.wikiIndex))}}}});

    const aliasEditor=$("#alias-editor"),aliasList=$("#alias-list"),aliasInput=$("#alias-input"),aliasForm=$("#alias-form");
    let aliases=aliasEditor?JSON.parse(aliasEditor.dataset.aliases||"[]"):[];
    function renderAliases() {{ if(!aliasList)return; aliasList.innerHTML=aliases.map((alias,i)=>`<span class="alias-chip">${{esc(alias)}}<button type="button" data-remove-alias="${{i}}" aria-label="Remove alias ${{esc(alias)}}">×</button></span>`).join("")||'<span class="meta">No aliases yet.</span>'; }}
    async function saveAliases() {{ const result=await api("/api/textstrata/item/{escape(item_id, quote=True)}/aliases",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{aliases}})}});aliases=result.aliases||aliases;renderAliases();toast("Aliases saved","success"); }}
    aliasForm?.addEventListener("submit",async e=>{{e.preventDefault();const value=aliasInput.value.trim();if(!value)return;if(!aliases.some(a=>a.toLowerCase()===value.toLowerCase()))aliases.push(value);aliasInput.value="";try{{await saveAliases()}}catch(error){{toast(error.message||"Alias save failed","error")}}}});
    aliasList?.addEventListener("click",async e=>{{const button=e.target.closest("[data-remove-alias]");if(!button)return;aliases.splice(Number(button.dataset.removeAlias),1);try{{await saveAliases()}}catch(error){{toast(error.message||"Alias save failed","error")}}}});
    renderAliases();

    function embedImage(ta, filename, url) {{
        const caption = filename.replace(/[.][^.]+$/, "").replace(/[-_]/g, " ");
        const mk = "![" + caption + "](" + url + ")";
        const pos = ta.selectionStart;
        ta.setRangeText(mk, pos, pos, "end");
        const captionStart = pos + 2;
        const captionEnd = captionStart + caption.length;
        ta.focus();
        ta.setSelectionRange(captionStart, captionEnd);
        editStatus.textContent = "Image embedded - selection is the caption, type to replace.";
        updatePreview();
        setTimeout(()=>{{if(editStatus.textContent.startsWith("Image"))editStatus.textContent=""}}, 3000);
    }}

    if (ta) ta.addEventListener("input", updatePreview);

    const humanCbox=document.getElementById("human-edit-cbox");
    saveBtn.onclick=async()=>{{saveBtn.disabled=true;editStatus.textContent="Saving...";try{{const headers={{"Content-Type":"text/plain; charset=utf-8"}};if(humanCbox&&humanCbox.checked)headers["X-TextStrata-Contributor"]="human";const d=await api("/api/textstrata/item/{escape(item_id, quote=True)}/save",{{method:"POST",headers,body:ta.value}});editStatus.textContent="Saved! Reloading...";toast("Saved successfully","success");setTimeout(()=>location.reload(),400)}}catch(e){{editStatus.textContent=e.message||"Save failed";toast(e.message||"Save failed","error");saveBtn.disabled=false}}}};
    const trashBtn=document.querySelector("[data-trash-item]");
    trashBtn?.addEventListener("click",async e=>{{if(!confirm("Move this item to trash? It can be restored from Tools > Trash."))return;const id=trashBtn.dataset.trashItem;try{{await api(`/api/textstrata/item/${{encodeURIComponent(id)}}/trash`,{{method:"POST",headers:{{"X-TextStrata-Confirm":"true"}}}});toast("Item moved to trash","success");setTimeout(()=>location.href="/",300);}}catch(e){{toast(e.message||"Delete failed","error");}}}});
    document.addEventListener("click",async e=>{{const button=e.target.closest("[data-remove-tag]");if(!button)return;const tag=button.dataset.removeTag;if(!confirm(`Remove tag "${{tag}}" from this note?`))return;button.disabled=true;try{{await api(`/api/textstrata/item/{escape(item_id, quote=True)}/tags/remove`,{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{tag}})}});toast("Tag removed","success");button.closest(".tag-chip")?.remove()}}catch(error){{toast(error.message||"Remove tag failed","error");button.disabled=false}}}});
    ta.addEventListener("paste",async e=>{{const items=e.clipboardData?.items;if(!items)return;for(const item of items){{if(!item.type.startsWith("image/"))continue;e.preventDefault();const file=item.getAsFile();if(!file)continue;editStatus.textContent="Uploading image...";try{{const fd=new FormData();fd.append("asset",file,file.name||"image.png");const a=await api("/api/asset/upload",{{method:"POST",body:fd}});embedImage(ta,file.name||"image.png",a.url);toast("Image uploaded","success")}}catch(e){{toast(e.message||"Upload failed","error")}}}}}});
    ta.addEventListener("drop",async e=>{{e.preventDefault();const files=e.dataTransfer?.files;if(!files||!files.length)return;for(const file of files){{if(!file.type.startsWith("image/"))continue;editStatus.textContent="Uploading image...";try{{const fd=new FormData();fd.append("asset",file,file.name||"image.png");const a=await api("/api/asset/upload",{{method:"POST",body:fd}});embedImage(ta,file.name||"image.png",a.url);toast("Image uploaded","success")}}catch(e){{toast(e.message||"Upload failed","error")}}}}}});

    document.addEventListener("keydown", function(e) {{
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") {{
        if (e.key === "Escape") {{ e.target.blur(); return; }}
        if ((e.ctrlKey || e.metaKey) && e.key === "s") {{
          e.preventDefault(); if (document.body.classList.contains("editing")) saveBtn?.click();
        }}
        return;
      }}
      const key = e.key;
      if (key === "e" || key === "E") {{ e.preventDefault(); editBtn?.click(); }}
      else if (key === "?") {{ e.preventDefault(); const h=document.getElementById("shortcuts-help"); if(h) h.hidden = !h.hidden; }}
      else if (key === "Escape") {{ const h=document.getElementById("shortcuts-help"); if(h && !h.hidden) h.hidden = true; else if(document.body.classList.contains("editing")) cancelBtn?.click(); }}
      else if (key === "/") {{ e.preventDefault(); const sq=document.querySelector("#sidebar-query"); if(sq) sq.focus(); }}
    }});

    // --- Menu bar ---
    const menuTriggers=document.querySelectorAll(".menu-trigger");
    function closeMenus(){{document.querySelectorAll(".menu-dropdown.open").forEach(m=>m.classList.remove("open"));document.querySelectorAll('.menu-trigger[aria-expanded]').forEach(b=>b.removeAttribute("aria-expanded"))}}
    menuTriggers.forEach(t=>{{
      t.addEventListener("click",e=>{{
        e.stopPropagation();
        const m=document.getElementById(t.dataset.menu);
        if(!m)return;
        const was=m.classList.contains("open");
        closeMenus();
        if(!was){{m.classList.add("open");t.setAttribute("aria-expanded","true")}}
      }})
    }});
    document.addEventListener("click",closeMenus);
    document.addEventListener("keydown",e=>{{if(e.key==="Escape")closeMenus()}});

    // --- Menu actions ---
    async function openAboutDialog(mode){{
      var d=document.getElementById("about-dialog"),title=document.getElementById("about-title"),content=document.getElementById("about-content");
      if(!d||!content)return;
      if(title)title.textContent=mode==="system-info"?"System info":"About";
      content.innerHTML='<p class="meta">Loading...</p>';
      try{{
        var info=await api("/api/textstrata/system-info"),settings=await api("/api/textstrata/settings"),it=info.install_type,paths=settings.paths||{{}};
        var summary='<table>'
          +'<tr><td>Version</td><td>'+esc(info.version)+'</td></tr>'
          +'<tr><td>Platform</td><td>'+esc(info.platform+" "+info.platform_release+" ("+info.architecture+")")+'</td></tr>'
          +'<tr><td>Install type</td><td>'+esc(it)+'</td></tr>'
          +'<tr><td>Process ID</td><td>'+esc(String(info.pid))+'</td></tr>'
          +'<tr><td>Docker</td><td>'+(info.docker?"Yes":"No")+'</td></tr>'
          +'<tr><td>systemd</td><td>'+(info.has_systemd?"Available":"Not available")+'</td></tr>'
          +'</table>';
        if(mode==="about"){{
          content.innerHTML='<p class="meta">TextStrata is a local knowledge workspace for notes, ingestion, and link discovery.</p>'+summary;
        }}else{{
          var sysList=[
            {{id:"linux-systemd",label:"Linux (systemd)",active:it==="linux-systemd",cmds:["systemctl --user restart textstrata-server","systemctl --user stop textstrata-server","systemctl --user status textstrata-server","journalctl --user -u textstrata-server -f"]}},
            {{id:"docker",label:"Docker",active:it==="docker",cmds:["docker restart <container>","docker stop <container>","docker logs <container>"]}},
            {{id:"macos",label:"macOS",active:it==="macos",cmds:["./textstrata restart","./textstrata web","pkill -f 'python3 -m textstrata'"]}},
            {{id:"windows",label:"Windows",active:it==="windows",cmds:["textstrata restart","textstrata web"]}},
            {{id:"other",label:"Other / Dev",active:!["linux-systemd","docker","macos","windows"].includes(it),cmds:["./textstrata restart","./textstrata web","./textstrata-server"]}},
          ];
          var instructions=sysList.map(function(s){{return '<div class="'+(s.active?"install-active":"install-other")+'"><strong>'+esc(s.label)+(s.active?' <span class="meta">(current)</span>':'')+'</strong><pre>'+esc(s.cmds.join("\\n"))+'</pre></div>'}}).join('');
          var pathRows=Object.entries(paths).map(function(entry){{return '<tr><td>'+esc(entry[0])+'</td><td><code>'+esc(String(entry[1]))+'</code></td></tr>'}}).join('');
          var pathTable=pathRows ? '<h3 style="margin:1rem 0 .5rem;font-family:var(--font-ui);font-size:.9rem">Storage paths</h3><table>'+pathRows+'</table>' : '';
          content.innerHTML=summary+'<h3 style="margin:1rem 0 .5rem;font-family:var(--font-ui);font-size:.9rem">Service management</h3>'+instructions+pathTable+'<p class="meta" style="margin-top:.5rem">The highlighted section matches your current install type.</p>';
        }}
      }}catch(e){{content.innerHTML='<p class="danger-text">Failed to load: '+esc(e.message)+'</p>'}}
      d.showModal();
    }}
    const aboutDlg=document.getElementById("about-dialog");
    const aboutTitle=document.getElementById("about-title");
    document.querySelectorAll("[data-action]").forEach(btn=>{{
      btn.addEventListener("click",async ()=>{{
        const a=btn.dataset.action;
        if(a==="new-note"){{ window.location.href="/new"; }}
        else if(a==="setup"){{ window.location.href="/setup"; }}
        else if(a==="settings"){{ const d=document.getElementById("settings-dialog"),s=document.getElementById("settings-open"); if(d&&!d.open)d.showModal(); if(s)s.click(); else if(!d)window.location.href="/?open=settings"; }}
        else if(a==="focus-mode")document.body.classList.toggle("focus-mode");
        else if(a==="graph")window.location.href="/graph";
        else if(a==="media")window.location.href="/media";
        else if(a==="review-queue")window.location.href="/?open=review";
        else if(a==="trash")window.location.href="/?open=trash";
        else if(a==="maintenance")window.location.href="/?open=maintenance";
        else if(a==="shortcuts"){{const h=document.getElementById("shortcuts-help");if(h)h.hidden=!h.hidden}}
        else if(a==="sync")window.location.href="/?open=imports";
        else if(a==="restart-server")window.location.href="/?open=maintenance";
        else if(a==="restart-engine")window.location.href="/?open=maintenance";
        else if(a==="docs"){{ fetch("/item/system.docs.help-system",{{method:"HEAD"}}).then(r=>window.location.href=r.ok?"/item/system.docs.help-system":"/item/system.operations-error-reference").catch(()=>window.location.href="/item/system.operations-error-reference") }}
        else if(a==="about"||a==="system-info"){{
          if(aboutTitle) aboutTitle.textContent = a==="about" ? "About" : "System info";
          try{{
            const info=await api("/api/textstrata/system-info");
            const settings=await api("/api/textstrata/settings");
            const paths=settings.paths||{{}};
            const it=info.install_type;
            const sysList=[
              {{id:"linux-systemd",label:"Linux (systemd)",active:it==="linux-systemd",cmds:["systemctl --user restart textstrata-server","systemctl --user stop textstrata-server","systemctl --user status textstrata-server","journalctl --user -u textstrata-server -f"]}},
              {{id:"docker",label:"Docker",active:it==="docker",cmds:["docker restart <container>","docker stop <container>","docker logs <container>"]}},
              {{id:"macos",label:"macOS",active:it==="macos",cmds:["./textstrata restart","./textstrata web","pkill -f 'python3 -m textstrata'"]}},
              {{id:"windows",label:"Windows",active:it==="windows",cmds:["textstrata restart","textstrata web"]}},
              {{id:"other",label:"Other / Dev",active:!["linux-systemd","docker","macos","windows"].includes(it),cmds:["./textstrata restart","./textstrata web","./textstrata-server"]}},
            ];
            const instructions=sysList.map(s=>
              '<div class="'+(s.active?"install-active":"install-other")+'">'
              +'<strong>'+esc(s.label)+(s.active?' <span class="meta">(current)</span>':"")+'</strong>'
              +'<pre>'+esc(s.cmds.join("\\n"))+'</pre></div>'
            ).join("");
            document.getElementById("about-content").innerHTML=
              '<table>'
              +'<tr><td>Version</td><td>'+esc(info.version)+'</td></tr>'
              +'<tr><td>Platform</td><td>'+esc(info.platform+' '+info.platform_release+' ('+info.architecture+')')+'</td></tr>'
              +'<tr><td>Install type</td><td>'+esc(it)+'</td></tr>'
              +'<tr><td>Process ID</td><td>'+esc(String(info.pid))+'</td></tr>'
              +'<tr><td>Docker</td><td>'+(info.docker?"Yes":"No")+'</td></tr>'
              +'<tr><td>systemd</td><td>'+(info.has_systemd?"Available":"Not available")+'</td></tr>'
              +'</table>'
              +'<h3 style="margin:1rem 0 .5rem;font-family:var(--font-ui);font-size:.9rem">Service management</h3>'
              +instructions
              +pathTable
              +'<p class="meta" style="margin-top:.5rem">The highlighted section matches your current install type.</p>';
          }}catch(e){{document.getElementById("about-content").innerHTML='<p class="danger-text">Failed to load: '+esc(e.message)+'</p>'}}
          aboutDlg.showModal();
        }}
      }})
    }});

    // --- Read Aloud (Web Speech API) ---
    var raBtn=document.getElementById("readaloud-btn"),raBar=document.getElementById("readaloud-bar"),raStatus=document.getElementById("readaloud-status"),raPp=document.getElementById("readaloud-playpause"),raStop=document.getElementById("readaloud-stop"),raRate=document.getElementById("readaloud-rate");
    if(!raBtn){{}}else if(!window.speechSynthesis){{
      raBtn.onclick=function(){{toast("Read Aloud requires a browser with speech synthesis support.","error",5000)}};
      raBtn.title="Speech synthesis not available";
      raBtn.style.opacity=".5"
    }}else{{
      var raUtterance=null,raChunks=[],raBlocks=[],raChunkIndex=0,raState="idle",raSession=0,raHighlighted=null;
      var raStartOffset=0,raBoundEndChunk=null,raBoundEndOffset=null;
      function raReady(){{try{{return window.speechSynthesis.getVoices().length>0}}catch(e){{return false}}}}
      function raTryVoices(cb){{
        if(raReady()){{cb();return}}
        var retries=0;
        function poll(){{
          if(raReady()){{cb();return}}
          retries++;
          if(retries<10){{setTimeout(poll,300)}}
          else{{toast("Speech synthesis not responding. On Linux try: sudo apt install speech-dispatcher","error",8000)}}
        }}
        poll()
      }}
      function raSplit(text){{
        var parts=text.match(/[^.!?\\n]+(?:[.!?]+|\\n+|$)/g)||[text],chunks=[],current="";
        parts.forEach(function(part){{
          part=part.trim();if(!part)return;
          if(current&&current.length+part.length+1>240){{chunks.push(current);current=""}}
          if(part.length>240){{if(current){{chunks.push(current);current=""}};for(var i=0;i<part.length;i+=240)chunks.push(part.slice(i,i+240))}}
          else current+=(current?" ":"")+part
        }});
        if(current)chunks.push(current);
        return chunks
      }}
      // Split the article into per-paragraph/heading "blocks" (rather than one
      // flat wall of text) so playback can jump straight to any block instead
      // of always restarting from the top -- this is what makes "read from
      // here" and resuming after an interruption possible.
      function raBuildBlocks(){{
        var bodyEl=document.querySelector(".body");
        raBlocks=[];raChunks=[];
        if(!bodyEl)return;
        var selector="p, li, h1, h2, h3, h4, h5, h6, blockquote, pre, td, th";
        var all=Array.prototype.slice.call(bodyEl.querySelectorAll(selector));
        function isNested(el){{
          var p=el.parentElement;
          while(p&&p!==bodyEl){{if(all.indexOf(p)!==-1)return true;p=p.parentElement}}
          return false
        }}
        all.filter(function(el){{return !isNested(el)}}).forEach(function(el){{
          var text=el.innerText||el.textContent||"";
          if(!text.trim())return;
          var parts=raSplit(text);
          if(!parts.length)return;
          var start=raChunks.length,offsets=[],acc=0;
          parts.forEach(function(c){{offsets.push(acc);raChunks.push(c);acc+=c.length+1}});
          el.dataset.raBlock=String(raBlocks.length);
          raBlocks.push({{el:el,start:start,end:raChunks.length,offsets:offsets}})
        }})
      }}
      function raBlockIndexForChunk(chunkIdx){{
        for(var i=0;i<raBlocks.length;i++){{if(chunkIdx>=raBlocks[i].start&&chunkIdx<raBlocks[i].end)return i}}
        return -1
      }}
      function raHighlight(chunkIdx){{
        var idx=raBlockIndexForChunk(chunkIdx),el=idx>=0?raBlocks[idx].el:null;
        if(raHighlighted&&raHighlighted!==el)raHighlighted.classList.remove("ra-reading");
        if(el){{el.classList.add("ra-reading");raHighlighted=el}}else{{raHighlighted=null}}
      }}
      function raClearHighlight(){{if(raHighlighted){{raHighlighted.classList.remove("ra-reading");raHighlighted=null}}}}
      function raProgressKey(){{return "textstrata-ra-progress:"+location.pathname}}
      function raSaveProgress(){{
        try{{
          if(raState==="idle"||raChunkIndex<=0||raChunkIndex>=raChunks.length){{localStorage.removeItem(raProgressKey());return}}
          localStorage.setItem(raProgressKey(),JSON.stringify({{chunk:raChunkIndex,total:raChunks.length,ts:Date.now()}}))
        }}catch(e){{}}
      }}
      function raClearProgress(){{try{{localStorage.removeItem(raProgressKey())}}catch(e){{}}}}
      function raReset(){{
        raSession++;
        raState="idle";
        if(raBtn){{raBtn.textContent="Read Aloud";raBtn.classList.remove("btn-active")}}
        if(raBar)raBar.hidden=true;
        if(raPp)raPp.textContent="Pause";
        try{{window.speechSynthesis.cancel()}}catch(e){{}}
        raUtterance=null;raChunkIndex=0;
        raStartOffset=0;raBoundEndChunk=null;raBoundEndOffset=null;
        raClearHighlight();raClearProgress()
      }}
      function raSpeakCurrent(){{
        if(raState!=="playing"||raChunkIndex>=raChunks.length){{if(raState==="playing")raReset();return}}
        raHighlight(raChunkIndex);
        var text=raChunks[raChunkIndex];
        // A "Read from here"/"Read selection" start can begin partway
        // through this chunk's text (raStartOffset) -- it only ever applies
        // to the very first chunk spoken, so it's consumed immediately.
        var startOffset=raStartOffset||0;raStartOffset=0;
        var bounded=raBoundEndChunk===raChunkIndex&&raBoundEndOffset!=null;
        var endOffset=bounded?raBoundEndOffset:text.length;
        if(startOffset>0||endOffset<text.length)text=text.slice(startOffset,Math.max(startOffset,endOffset));
        if(!text.trim()){{if(bounded){{raReset();return}}raChunkIndex++;raSpeakCurrent();return}}
        var session=raSession,utter=new SpeechSynthesisUtterance(text);
        utter.rate=parseFloat(raRate?raRate.value:"1");
        utter.onstart=function(){{
          if(session!==raSession)return;
          if(raBtn){{raBtn.textContent="Stop";raBtn.classList.add("btn-active")}}
          if(raBar)raBar.hidden=false;
          if(raStatus)raStatus.textContent=bounded?"Reading selection...":"Reading...";
          if(raPp)raPp.textContent="Pause"
        }};
        utter.onend=function(){{
          if(session!==raSession||raState!=="playing")return;
          if(bounded){{raReset();return}}
          raChunkIndex++;raSaveProgress();raSpeakCurrent()
        }};
        utter.onerror=function(e){{
          if(session!==raSession||raState!=="playing"||e.error==="canceled"||e.error==="interrupted")return;
          raReset();
          if(navigator.userAgent.indexOf("Linux")>-1||navigator.platform.indexOf("Linux")>-1){{
            toast("Read Aloud needs speech-dispatcher. Run: sudo apt install speech-dispatcher","error",8000)
          }}else{{toast("Read Aloud error: "+e.error+". Try a different browser.","error",5000)}}
        }};
        raUtterance=utter;
        window.speechSynthesis.speak(utter)
      }}
      // Shared entry point for the top toolbar button (index 0) and
      // resuming a saved position -- unbounded, continues to the end.
      function raStartAt(chunkIndex){{
        raBuildBlocks();
        if(!raChunks.length){{toast("Page has no text content.","error");return}}
        var idx=Math.max(0,Math.min(chunkIndex||0,raChunks.length-1));
        raStartOffset=0;raBoundEndChunk=null;raBoundEndOffset=null;
        raTryVoices(function(){{raChunkIndex=idx;raState="playing";raSession++;raSpeakCurrent()}})
      }}
      // Reading a specific text selection: starts exactly at the selection's
      // start and stops exactly at its end, instead of continuing through
      // the rest of the article -- a selection is a bounded request, not
      // just a bookmark.
      function raStartSelection(startPos,endPos){{
        raBuildBlocks();
        if(!raChunks.length){{toast("Page has no text content.","error");return}}
        raTryVoices(function(){{
          raChunkIndex=startPos.chunk;
          raStartOffset=startPos.offset;
          raBoundEndChunk=endPos.chunk;
          raBoundEndOffset=endPos.offset;
          raState="playing";raSession++;raSpeakCurrent()
        }})
      }}
      raBtn.onclick=function(){{
        if(raState!=="idle"){{raReset();return}}
        raStartAt(0)
      }};
      if(raPp)raPp.onclick=function(){{
        if(raState==="playing"){{raState="paused";raSession++;window.speechSynthesis.cancel();raPp.textContent="Resume";if(raStatus)raStatus.textContent="Paused";raSaveProgress()}}
        else if(raState==="paused"){{raState="playing";raSession++;raSpeakCurrent()}}
      }};
      if(raStop)raStop.onclick=raReset;
      if(raRate)raRate.onchange=function(){{if(raState==="playing"){{raSession++;window.speechSynthesis.cancel();raSpeakCurrent()}}}}
      window.addEventListener("beforeunload",function(){{if(raState==="playing"||raState==="paused")raSaveProgress()}});

      // --- "Read from here": select any text and a floating action reads
      // exactly that selection -- starting precisely where it begins and
      // stopping precisely where it ends, not spilling into the rest of
      // the article. ---
      var raSelTip=document.getElementById("ra-select-tip"),raSelTipBtn=raSelTip?raSelTip.querySelector("button"):null,raPendingSelection=null;
      function raTextOffsetInBlock(blockEl,node,offset){{
        try{{var range=document.createRange();range.selectNodeContents(blockEl);range.setEnd(node,offset);return range.toString().length}}catch(e){{return null}}
      }}
      // Position of one selection boundary (start or end) as {{chunk,offset}}:
      // chunk is the global chunk index, offset is the (approximate)
      // character position within that chunk's own text.
      function raPositionAt(node,offset){{
        if(!node)return null;
        var startEl=node.nodeType===1?node:node.parentElement;
        var blockEl=startEl&&startEl.closest?startEl.closest("[data-ra-block]"):null;
        if(!blockEl)return null;
        var block=raBlocks[parseInt(blockEl.dataset.raBlock,10)];
        if(!block)return null;
        var textOffset=raTextOffsetInBlock(blockEl,node,offset);
        if(textOffset==null)return {{chunk:block.start,offset:0}};
        var localIdx=0;
        for(var i=0;i<block.offsets.length;i++){{if(block.offsets[i]<=textOffset)localIdx=i;else break}}
        return {{chunk:block.start+localIdx,offset:Math.max(0,textOffset-block.offsets[localIdx])}}
      }}
      function raPositionsForSelection(){{
        var sel=window.getSelection();
        if(!sel||sel.rangeCount===0||sel.isCollapsed)return null;
        var range=sel.getRangeAt(0);
        var startPos=raPositionAt(range.startContainer,range.startOffset);
        var endPos=raPositionAt(range.endContainer,range.endOffset);
        if(!startPos||!endPos)return null;
        if(endPos.chunk<startPos.chunk||(endPos.chunk===startPos.chunk&&endPos.offset<startPos.offset)){{
          var tmp=startPos;startPos=endPos;endPos=tmp
        }}
        return {{start:startPos,end:endPos}}
      }}
      function raHideSelTip(){{if(raSelTip)raSelTip.hidden=true;raPendingSelection=null}}
      function raShowSelTipForSelection(){{
        var sel=window.getSelection();
        if(!sel||sel.rangeCount===0||sel.isCollapsed){{raHideSelTip();return}}
        var bodyEl=document.querySelector(".body");
        var range=sel.getRangeAt(0);
        if(!bodyEl||!bodyEl.contains(range.commonAncestorContainer)){{raHideSelTip();return}}
        raBuildBlocks();
        var positions=raPositionsForSelection();
        if(!positions){{raHideSelTip();return}}
        var rect=range.getBoundingClientRect();
        if(!rect||(rect.width===0&&rect.height===0)){{raHideSelTip();return}}
        raPendingSelection=positions;
        if(raSelTip){{raSelTip.style.left=(rect.left+rect.width/2)+"px";raSelTip.style.top=rect.top+"px";raSelTip.hidden=false}}
      }}
      document.addEventListener("mouseup",function(e){{if(raSelTip&&raSelTip.contains(e.target))return;setTimeout(raShowSelTipForSelection,0)}});
      document.addEventListener("keyup",function(e){{if(e.shiftKey)setTimeout(raShowSelTipForSelection,0)}});
      document.addEventListener("scroll",raHideSelTip,true);
      document.addEventListener("mousedown",function(e){{if(raSelTip&&!raSelTip.contains(e.target))raHideSelTip()}});
      if(raSelTipBtn)raSelTipBtn.onclick=function(){{
        if(!raPendingSelection)return;
        var positions=raPendingSelection;
        raHideSelTip();
        try{{window.getSelection().removeAllRanges()}}catch(e){{}}
        raStartSelection(positions.start,positions.end)
      }};

      // --- Resume after an interruption (closed tab, reload, navigated away
      // mid-article) instead of always starting over from the beginning. ---
      function raTryRestore(){{
        var raw;try{{raw=localStorage.getItem(raProgressKey())}}catch(e){{raw=null}}
        if(!raw)return;
        var saved;try{{saved=JSON.parse(raw)}}catch(e){{return}}
        if(!saved||typeof saved.chunk!=="number"||saved.total!==raChunks.length||saved.chunk<=0||saved.chunk>=raChunks.length){{raClearProgress();return}}
        raChunkIndex=saved.chunk;
        raState="paused";
        if(raBar)raBar.hidden=false;
        if(raBtn){{raBtn.textContent="Stop";raBtn.classList.add("btn-active")}}
        if(raPp)raPp.textContent="Resume";
        if(raStatus)raStatus.textContent="Paused at "+Math.round(saved.chunk/saved.total*100)+"% \\u2014 resume where you left off";
        raHighlight(raChunkIndex)
      }}
      raBuildBlocks();
      raTryRestore()
    }}
  }})();
  </script>
"""
