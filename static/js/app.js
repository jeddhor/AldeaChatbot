// Global in-memory conversation state.
let currentMessages = [];
let loadedChatId = null;
let lastAssistantReply = "";
let cachedSettings = null;
let ttsAudioElement = null;
let ttsObjectUrl = null;
let ttsBusy = false;
let ttsAbortRequested = false;
let ttsFetchController = null;
let sttRecorder = null;
let sttStream = null;
let sttChunks = [];
let sttRecording = false;
let sttBusy = false;
let voiceToggleEnabled = false;
let sttAudioContext = null;
let sttSourceNode = null;
let sttAnalyser = null;
let sttMonitorInterval = null;
let sttLastSpeechMs = 0;
let sttSpeechStartedMs = 0;
let speakRepliesEnabled = false;
let speakReplyFromBubble = null;
let openChatMenu = null;
let openChatMenuTrigger = null;

function qs(id) {
    return document.getElementById(id);
}

function escapeHtml(value) {
    const div = document.createElement("div");
    div.innerText = value;
    return div.innerHTML;
}

function renderAssistantMarkdown(content) {
    const raw = String(content || "");

    // Graceful fallback when CDN scripts are unavailable.
    if (!window.marked || !window.DOMPurify) {
        return escapeHtml(raw).replace(/\n/g, "<br>");
    }

    marked.setOptions({
        gfm: true,
        breaks: true,
    });

    const unsafeHtml = marked.parse(raw);
    return window.DOMPurify.sanitize(unsafeHtml);
}

function copyTextToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
    }

    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "absolute";
    area.style.left = "-9999px";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    document.body.removeChild(area);
    return Promise.resolve();
}

function buildCopyIconSvg() {
    return `
<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
  <path d="M9 9h10v12H9z"></path>
  <path d="M5 3h10v2H7v10H5z"></path>
</svg>`;
}

function buildSpeakIconSvg() {
        return `
<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <path d="M3 9v6h4l5 4V5L7 9H3z"></path>
    <path d="M15.5 8.5a1 1 0 0 1 1.4 0 5 5 0 0 1 0 7 1 1 0 1 1-1.4-1.4 3 3 0 0 0 0-4.2 1 1 0 0 1 0-1.4z"></path>
    <path d="M18.3 5.7a1 1 0 0 1 1.4 0 9 9 0 0 1 0 12.7 1 1 0 1 1-1.4-1.4 7 7 0 0 0 0-9.9 1 1 0 0 1 0-1.4z"></path>
</svg>`;
}

function buildHamburgerIconSvg() {
    return `
<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <circle cx="12" cy="5" r="1.9"></circle>
    <circle cx="12" cy="12" r="1.9"></circle>
    <circle cx="12" cy="19" r="1.9"></circle>
</svg>`;
}

function closeChatMenu() {
    if (openChatMenu) {
        openChatMenu.remove();
        openChatMenu = null;
    }
    if (openChatMenuTrigger) {
        openChatMenuTrigger.setAttribute("aria-expanded", "false");
        openChatMenuTrigger = null;
    }
}

function positionChatMenu(menu, trigger) {
    const rect = trigger.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();

    let left = rect.right - menuRect.width;
    left = Math.max(8, Math.min(left, window.innerWidth - menuRect.width - 8));

    let top = rect.bottom + 6;
    if (top + menuRect.height > window.innerHeight - 8) {
        top = Math.max(8, rect.top - menuRect.height - 6);
    }

    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
}

function openChatActionsMenu(trigger, chat) {
    const sameMenuRequested = openChatMenuTrigger === trigger;
    closeChatMenu();
    if (sameMenuRequested) {
        return;
    }

    const menu = document.createElement("div");
    menu.className = "chat-floating-menu";
    menu.setAttribute("role", "menu");

    const regenItem = document.createElement("button");
    regenItem.type = "button";
    regenItem.className = "chat-menu-item";
    regenItem.textContent = "Regenerate Title";
    regenItem.setAttribute("role", "menuitem");
    regenItem.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        regenItem.disabled = true;
        try {
            const response = await fetch(`/api/chats/${chat.id}/regenerate-title`, { method: "POST" });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || "Failed to regenerate title.");
            }
            closeChatMenu();
            await loadChats();
        } catch (error) {
            alert(error.message || "Failed to regenerate title.");
        } finally {
            regenItem.disabled = false;
        }
    });

    const renameItem = document.createElement("button");
    renameItem.type = "button";
    renameItem.className = "chat-menu-item";
    renameItem.textContent = "Custom Title";
    renameItem.setAttribute("role", "menuitem");
    renameItem.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();

        const customTitle = (prompt("Enter a custom title:", chat.title) || "").trim();
        if (!customTitle) return;

        const response = await fetch(`/api/chats/${chat.id}/title`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: customTitle }),
        });
        const data = await response.json();
        if (!response.ok) {
            alert(data.error || "Failed to update title.");
            return;
        }

        closeChatMenu();
        await loadChats();
    });

    const deleteItem = document.createElement("button");
    deleteItem.type = "button";
    deleteItem.className = "chat-menu-item danger";
    deleteItem.textContent = "Delete Conversation";
    deleteItem.setAttribute("role", "menuitem");
    deleteItem.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const skipConfirm = Boolean(event.shiftKey);
        if (!skipConfirm && !confirm(`Delete chat '${chat.title}'?`)) return;

        await fetch(`/api/chats/${chat.id}`, { method: "DELETE" });
        if (loadedChatId === chat.id) {
            loadedChatId = null;
            currentMessages = [];
            renderHistory(currentMessages);
        }
        closeChatMenu();
        await loadChats();
    });

    menu.appendChild(regenItem);
    menu.appendChild(renameItem);
    menu.appendChild(deleteItem);
    menu.addEventListener("click", (event) => event.stopPropagation());

    document.body.appendChild(menu);
    openChatMenu = menu;
    openChatMenuTrigger = trigger;
    trigger.setAttribute("aria-expanded", "true");
    positionChatMenu(menu, trigger);
}

function enhanceMarkdownCodeBlocks(container) {
    if (!container) return;

    const codeBlocks = container.querySelectorAll("pre > code");
    codeBlocks.forEach((codeBlock) => {
        if (window.hljs) {
            window.hljs.highlightElement(codeBlock);
        }

        const pre = codeBlock.parentElement;
        if (!pre || pre.dataset.copyReady === "true") {
            return;
        }

        pre.dataset.copyReady = "true";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "code-copy-btn";
        button.innerHTML = buildCopyIconSvg();
        button.dataset.tooltip = "Copy";
        button.title = "Copy";
        button.setAttribute("aria-label", "Copy code");

        button.addEventListener("mouseenter", () => {
            if (!button.classList.contains("copied")) {
                button.dataset.tooltip = "Copy";
            }
        });

        button.addEventListener("mouseleave", () => {
            if (!button.classList.contains("copied")) {
                button.dataset.tooltip = "Copy";
            }
        });

        button.addEventListener("click", async () => {
            try {
                await copyTextToClipboard(codeBlock.innerText || "");
                button.classList.add("copied");
                button.dataset.tooltip = "Copied";
                setTimeout(() => {
                    button.classList.remove("copied");
                    button.dataset.tooltip = "Copy";
                }, 900);
            } catch (error) {
                console.warn("Copy failed", error);
            }
        });

        pre.appendChild(button);
    });
}

function resolveAvatar(url, fallbackSeed) {
    if (url && url.trim()) return url.trim();
    return `https://api.dicebear.com/9.x/bottts/svg?seed=${encodeURIComponent(fallbackSeed)}`;
}

function getTtsAudioElement() {
    if (ttsAudioElement) return ttsAudioElement;

    ttsAudioElement = document.createElement("audio");
    ttsAudioElement.preload = "auto";
    ttsAudioElement.style.display = "none";
    document.body.appendChild(ttsAudioElement);
    return ttsAudioElement;
}

function isBenignPlayInterruption(error) {
    const name = String(error?.name || "");
    const message = String(error?.message || "").toLowerCase();
    return (
        name === "AbortError" ||
        message.includes("play() request was interrupted") ||
        message.includes("media was removed from the document")
    );
}

function setTtsSource(url, isObjectUrl = false) {
    const audio = getTtsAudioElement();

    // If a previous blob URL exists, revoke it before replacing the source.
    if (ttsObjectUrl) {
        URL.revokeObjectURL(ttsObjectUrl);
        ttsObjectUrl = null;
    }

    // Stop currently playing audio before switching source.
    if (!audio.paused) {
        audio.pause();
        audio.currentTime = 0;
    }

    audio.src = url;
    if (isObjectUrl) {
        ttsObjectUrl = url;
    }
    audio.load();
}

async function playCurrentTtsSource() {
    const audio = getTtsAudioElement();
    try {
        await audio.play();
    } catch (error) {
        // Chromium can throw an interruption error even when playback proceeds.
        if (!isBenignPlayInterruption(error)) {
            throw error;
        }
    }
}

async function playCurrentTtsSourceToEnd() {
    const audio = getTtsAudioElement();

    // Start playback first; this can resolve once playback starts.
    await playCurrentTtsSource();

    // Wait until the current chunk finishes for natural sequential narration.
    if (audio.ended || audio.paused) {
        return;
    }

    await new Promise((resolve, reject) => {
        const onEnded = () => {
            cleanup();
            resolve();
        };
        const onPause = () => {
            if (ttsAbortRequested) {
                cleanup();
                resolve();
            }
        };
        const onError = () => {
            cleanup();
            reject(new Error("Audio playback failed."));
        };
        const cleanup = () => {
            audio.removeEventListener("ended", onEnded);
            audio.removeEventListener("pause", onPause);
            audio.removeEventListener("error", onError);
        };

        audio.addEventListener("ended", onEnded, { once: true });
        audio.addEventListener("pause", onPause);
        audio.addEventListener("error", onError, { once: true });
    });
}

function splitByWordBudget(text, maxChars) {
    const words = text.trim().split(/\s+/).filter(Boolean);
    if (!words.length) return [];

    const chunks = [];
    let current = "";

    for (const word of words) {
        const candidate = current ? `${current} ${word}` : word;
        if (candidate.length <= maxChars) {
            current = candidate;
        } else {
            if (current) {
                chunks.push(current);
            }
            current = word;
        }
    }

    if (current) {
        chunks.push(current);
    }

    return chunks;
}

function splitLongSegment(segment, targetChars = 170, maxChars = 250) {
    const trimmed = segment.trim();
    if (!trimmed) return [];

    // Prefer natural phrasing breaks before hard word-budget fallback.
    const phraseParts = trimmed
        .split(/(?<=[,;:])\s+/)
        .map((part) => part.trim())
        .filter(Boolean);

    if (phraseParts.length <= 1) {
        return splitByWordBudget(trimmed, maxChars);
    }

    const chunks = [];
    let current = "";

    for (const part of phraseParts) {
        if (part.length > maxChars) {
            if (current) {
                chunks.push(current);
                current = "";
            }
            chunks.push(...splitByWordBudget(part, maxChars));
            continue;
        }

        const candidate = current ? `${current} ${part}` : part;
        if (candidate.length <= targetChars || !current) {
            current = candidate;
        } else {
            chunks.push(current);
            current = part;
        }
    }

    if (current) {
        chunks.push(current);
    }

    // Merge very short tails into previous chunk when possible.
    const merged = [];
    for (const chunk of chunks) {
        if (!merged.length) {
            merged.push(chunk);
            continue;
        }

        if (chunk.length < 40 && `${merged[merged.length - 1]} ${chunk}`.length <= maxChars) {
            merged[merged.length - 1] = `${merged[merged.length - 1]} ${chunk}`;
        } else {
            merged.push(chunk);
        }
    }

    return merged;
}

function splitReplyIntoTtsChunks(text) {
    const normalized = String(text || "").replace(/\s+/g, " ").trim();
    if (!normalized) return [];

    // Sentence-first splitting keeps cadence natural for narration.
    const sentences = normalized.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [normalized];
    const chunks = [];

    for (const sentence of sentences) {
        // Slightly smaller chunk targets reduce long pauses on slower TTS hardware.
        chunks.push(...splitLongSegment(sentence, 120, 170));
    }

    return chunks.filter(Boolean);
}

function toAsciiTtsText(text) {
    const replacements = [
        [/\u2018|\u2019|\u201A|\u201B/g, "'"],
        [/\u201C|\u201D|\u201E/g, '"'],
        [/\u2013|\u2014|\u2015|\u2212/g, "-"],
        [/\u2026/g, "..."],
        [/\u00A0/g, " "],
        [/\u2022|\u25CF|\u25E6/g, "*"],
        [/\u00AB|\u00BB/g, '"'],
        [/\u2032/g, "'"],
        [/\u2033/g, '"'],
    ];

    let out = String(text || "");
    for (const [pattern, replacement] of replacements) {
        out = out.replace(pattern, replacement);
    }

    // Strip any remaining non-ASCII to guarantee payload safety for TTS backend.
    out = out.replace(/[^\x20-\x7E\n\r\t]/g, "");
    out = out.replace(/[ \t]+/g, " ").replace(/\s*\n\s*/g, " ").trim();
    return out;
}

async function requestTtsAudio(text) {
    const safeText = toAsciiTtsText(text);
    if (!safeText) {
        throw new Error("TTS text was empty after ASCII normalization.");
    }

    const controller = new AbortController();
    ttsFetchController = controller;

    try {
        const response = await fetch("/api/tts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: safeText }),
            signal: controller.signal,
        });

        const contentType = response.headers.get("content-type") || "";
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error || "Text-to-speech request failed");
        }

        if (contentType.includes("application/json")) {
            const data = await response.json();
            const audioUrl = data.audio_url || data.url;
            if (audioUrl) {
                return { source: audioUrl, isObjectUrl: false };
            }
            throw new Error("TTS JSON response had no playable URL.");
        }

        const blob = await response.blob();
        return { source: URL.createObjectURL(blob), isObjectUrl: true };
    } finally {
        if (ttsFetchController === controller) {
            ttsFetchController = null;
        }
    }
}

async function speakTextChunked(text) {
    const chunks = splitReplyIntoTtsChunks(text);
    if (!chunks.length) {
        return;
    }

    // Prime the first request, then prefetch one chunk ahead while current chunk plays.
    let pendingAudioPromise = requestTtsAudio(chunks[0]);

    try {
        for (let index = 0; index < chunks.length; index += 1) {
            if (ttsAbortRequested) {
                return;
            }

            const prepared = await pendingAudioPromise;

            if (index + 1 < chunks.length) {
                pendingAudioPromise = requestTtsAudio(chunks[index + 1]);
            } else {
                pendingAudioPromise = null;
            }

            setTtsSource(prepared.source, prepared.isObjectUrl);
            await playCurrentTtsSourceToEnd();

            if (ttsAbortRequested) {
                return;
            }
        }
    } catch (error) {
        if (ttsAbortRequested || error?.name === "AbortError") {
            return;
        }
        throw error;
    }
}

async function fetchSettings() {
    const response = await fetch("/api/settings");
    if (!response.ok) throw new Error("Unable to load settings");
    cachedSettings = await response.json();
    return cachedSettings;
}

function formatMessageTimestamp(timestampValue) {
    const parsed = timestampValue ? new Date(timestampValue) : new Date();
    const value = Number.isNaN(parsed.getTime()) ? new Date() : parsed;

    const shortDate = value.toLocaleDateString("en-US", {
        month: "2-digit",
        day: "2-digit",
        year: "numeric",
    });
    const shortTime = value.toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
    });
    const fullDateTime = value.toLocaleString("en-US", {
        weekday: "long",
        month: "long",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
    });

    return {
        short: `${shortDate} ${shortTime}`,
        full: fullDateTime,
    };
}

function appendMessage(role, content, timestampValue = null) {
    const container = qs("chatMessages");
    if (!container) return;

    const settings = cachedSettings || {};
    const assistantAvatar = resolveAvatar(settings?.ui?.assistant_avatar, "Sentinel");
    const userAvatar = resolveAvatar(settings?.ui?.user_avatar, "Operator");

    const wrapper = document.createElement("div");
    wrapper.className = `msg ${role === "user" ? "msg-user" : "msg-assistant"}`;

    const avatar = document.createElement("img");
    avatar.className = "msg-avatar";
    avatar.alt = role === "user" ? "User" : "Assistant";
    avatar.referrerPolicy = "no-referrer";
    avatar.src = role === "user" ? userAvatar : assistantAvatar;

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";

    if (role === "assistant") {
        bubble.classList.add("markdown-body");
        bubble.innerHTML = renderAssistantMarkdown(content);
        enhanceMarkdownCodeBlocks(bubble);

        const speakBtn = document.createElement("button");
        speakBtn.type = "button";
        speakBtn.className = "assistant-speak-btn";
        speakBtn.innerHTML = buildSpeakIconSvg();
        speakBtn.dataset.tooltip = "Speak";
        speakBtn.title = "Speak this reply";
        speakBtn.setAttribute("aria-label", "Speak this reply");
        speakBtn.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();

            if (typeof speakReplyFromBubble !== "function") {
                alert("Text to speech is not ready yet.");
                return;
            }

            speakBtn.disabled = true;
            speakBtn.classList.add("speaking");
            speakBtn.dataset.tooltip = "Speaking";

            try {
                await speakReplyFromBubble(content);
                speakBtn.dataset.tooltip = "Spoken";
                setTimeout(() => {
                    if (!speakBtn.classList.contains("speaking")) {
                        speakBtn.dataset.tooltip = "Speak";
                    }
                }, 900);
            } finally {
                speakBtn.disabled = false;
                speakBtn.classList.remove("speaking");
            }
        });
        bubble.appendChild(speakBtn);
    } else {
        bubble.innerHTML = escapeHtml(content);
    }

    const timestamp = document.createElement("span");
    const timeMeta = formatMessageTimestamp(timestampValue);
    timestamp.className = "msg-time";
    timestamp.textContent = timeMeta.short;
    timestamp.title = timeMeta.full;
    timestamp.setAttribute("aria-label", timeMeta.full);
    bubble.appendChild(timestamp);

    wrapper.appendChild(avatar);
    wrapper.appendChild(bubble);
    container.appendChild(wrapper);
    container.scrollTop = container.scrollHeight;
}

function renderHistory(messages) {
    const container = qs("chatMessages");
    if (!container) return;
    container.innerHTML = "";
    messages.forEach((m) => appendMessage(m.role, m.content, m.created_at));
}

async function sendChat(message) {
    const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            message,
            history: currentMessages,
        }),
    });

    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || "Chat request failed");
    }

    return data;
}

async function loadChats() {
    const list = qs("chatList");
    if (!list) return;

    const response = await fetch("/api/chats");
    const chats = await response.json();

    list.innerHTML = "";
    chats.forEach((chat) => {
        const li = document.createElement("li");
        li.className = "chat-list-item";

        const openBtn = document.createElement("button");
        openBtn.textContent = chat.title;
        openBtn.className = "chat-open-btn";
        openBtn.title = "Restore chat";
        openBtn.addEventListener("click", async () => {
            await restoreChat(chat.id);
        });

        const actions = document.createElement("div");
        actions.className = "chat-item-actions";

        const menuBtn = document.createElement("button");
        menuBtn.type = "button";
        menuBtn.className = "btn btn-silver chat-menu-btn";
        menuBtn.title = "Conversation actions";
        menuBtn.setAttribute("aria-label", `Actions for ${chat.title}`);
        menuBtn.setAttribute("aria-haspopup", "menu");
        menuBtn.setAttribute("aria-expanded", "false");
        menuBtn.innerHTML = buildHamburgerIconSvg();

        menuBtn.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            openChatActionsMenu(menuBtn, chat);
        });

        li.appendChild(openBtn);
        actions.appendChild(menuBtn);
        li.appendChild(actions);
        list.appendChild(li);
    });
}

async function restoreChat(chatId) {
    const response = await fetch(`/api/chats/${chatId}`);
    const data = await response.json();
    if (!response.ok) {
        alert(data.error || "Failed to restore chat.");
        return;
    }

    loadedChatId = chatId;
    currentMessages = data.messages.map((m) => ({ role: m.role, content: m.content, created_at: m.created_at }));
    const assistants = currentMessages.filter((m) => m.role === "assistant");
    lastAssistantReply = assistants.length ? assistants[assistants.length - 1].content : "";
    renderHistory(currentMessages);
}

async function saveCurrentChat() {
    const saveButton = qs("saveChatBtn");

    function setSaveButtonState(isSaving) {
        if (!saveButton) return;
        saveButton.disabled = isSaving;
        saveButton.classList.toggle("is-saving", isSaving);
        saveButton.textContent = isSaving ? "Saving..." : "Save Current Chat";
    }

    if (!currentMessages.length) {
        alert("No messages to save yet.");
        return;
    }

    const payload = { messages: currentMessages };
    const method = loadedChatId ? "PUT" : "POST";
    const url = loadedChatId ? `/api/chats/${loadedChatId}` : "/api/chats";

    setSaveButtonState(true);
    try {
        const response = await fetch(url, {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await response.json();
        if (!response.ok) {
            alert(data.error || "Failed to save chat.");
            return;
        }

        if (!loadedChatId && data.chat_id) {
            loadedChatId = data.chat_id;
        }

        await loadChats();
    } finally {
        setSaveButtonState(false);
    }
}

async function transcribeAudio(file) {
    const form = new FormData();
    const filename = file?.name || "voice-input.webm";
    form.append("audio", file, filename);

    const response = await fetch("/api/stt", {
        method: "POST",
        body: form,
    });

    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || "Speech transcription failed");
    }
    return data.text || "";
}

function chooseRecordingMimeType() {
    if (typeof MediaRecorder === "undefined") return "";

    const preferred = [
        "audio/wav",
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/mp4",
    ];

    for (const mime of preferred) {
        if (MediaRecorder.isTypeSupported(mime)) {
            return mime;
        }
    }

    return "";
}

function stopRecordingStream() {
    if (!sttStream) return;
    sttStream.getTracks().forEach((track) => track.stop());
    sttStream = null;
}

function audioBufferToWavBlob(audioBuffer) {
    const channels = audioBuffer.numberOfChannels;
    const sampleRate = audioBuffer.sampleRate;
    const length = audioBuffer.length;

    // Down-mix to mono because it is broadly compatible for STT services.
    const mono = new Float32Array(length);
    for (let channel = 0; channel < channels; channel += 1) {
        const input = audioBuffer.getChannelData(channel);
        for (let i = 0; i < length; i += 1) {
            mono[i] += input[i] / channels;
        }
    }

    const bytesPerSample = 2;
    const blockAlign = bytesPerSample;
    const byteRate = sampleRate * blockAlign;
    const dataSize = length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    let offset = 0;
    const writeString = (value) => {
        for (let i = 0; i < value.length; i += 1) {
            view.setUint8(offset, value.charCodeAt(i));
            offset += 1;
        }
    };

    writeString("RIFF");
    view.setUint32(offset, 36 + dataSize, true);
    offset += 4;
    writeString("WAVE");
    writeString("fmt ");
    view.setUint32(offset, 16, true);
    offset += 4;
    view.setUint16(offset, 1, true); // PCM
    offset += 2;
    view.setUint16(offset, 1, true); // mono
    offset += 2;
    view.setUint32(offset, sampleRate, true);
    offset += 4;
    view.setUint32(offset, byteRate, true);
    offset += 4;
    view.setUint16(offset, blockAlign, true);
    offset += 2;
    view.setUint16(offset, 16, true); // bits/sample
    offset += 2;
    writeString("data");
    view.setUint32(offset, dataSize, true);
    offset += 4;

    // Convert float samples [-1, 1] to 16-bit PCM.
    for (let i = 0; i < mono.length; i += 1) {
        const s = Math.max(-1, Math.min(1, mono[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
        offset += 2;
    }

    return new Blob([buffer], { type: "audio/wav" });
}

async function convertBlobToWav(blob) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) {
        throw new Error("Browser audio conversion is not supported.");
    }

    const audioContext = new AudioCtx();
    try {
        const arrayBuffer = await blob.arrayBuffer();
        const decoded = await audioContext.decodeAudioData(arrayBuffer.slice(0));
        return audioBufferToWavBlob(decoded);
    } finally {
        await audioContext.close();
    }
}

async function speakText(text) {
    await speakTextChunked(text);
}

function applyTheme(nextTheme) {
    document.documentElement.setAttribute("data-theme", nextTheme);
}

function setupThemeToggle() {
    const current = document.documentElement.getAttribute("data-theme") || "matrix-green";
    applyTheme(current);
}

async function initChatPage() {
    if (!qs("chatForm")) return;

    await fetchSettings();

    const userAvatar = qs("userAvatar");
    if (userAvatar) {
        userAvatar.src = resolveAvatar(cachedSettings?.ui?.user_avatar, "Operator");
    }

    await loadChats();

    const chatForm = qs("chatForm");
    const chatInput = qs("chatInput");
    const saveChatBtn = qs("saveChatBtn");
    const newChatBtn = qs("newChatBtn");
    const transcribeSpeechToggle = qs("transcribeSpeechToggle");
    const speakRepliesToggle = qs("speakRepliesToggle");
    const stopTtsBtn = qs("stopTtsBtn");
    const toolActivityBadge = qs("toolActivityBadge");
    const toolActivityLabel = qs("toolActivityLabel");
    const wakeWordBadge = qs("wakeWordBadge");
    const wakeWordLabel = qs("wakeWordLabel");

    const configuredVadThreshold = Number(cachedSettings?.stt?.vad_threshold);
    const configuredSilenceTimeoutMs = Number(cachedSettings?.stt?.silence_timeout_ms);
    const configuredRandomIdleSeconds = Number(cachedSettings?.random_chats?.idle_seconds);
    const configuredAutoSendSilenceMs = Number(cachedSettings?.stt?.auto_send_silence_ms);
    const WAKE_WORD_ENABLED = Boolean(cachedSettings?.stt?.wake_word_activation);
    const AUTO_SEND_ON_SILENCE = Boolean(cachedSettings?.stt?.auto_send_on_silence_detected);
    const WAKE_WORD = "computer";
    const WAKE_WORD_REGEX = /\bcomputer\b/i;
    const WakeWordRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const VAD_THRESHOLD = Number.isFinite(configuredVadThreshold)
        ? Math.min(0.2, Math.max(0.001, configuredVadThreshold))
        : 0.018;
    const VAD_SILENCE_MS = Number.isFinite(configuredSilenceTimeoutMs)
        ? Math.min(5000, Math.max(200, Math.round(configuredSilenceTimeoutMs)))
        : 900;
    const VAD_MIN_SPEECH_MS = 450;
    const VAD_INTERVAL_MS = 120;
    const RANDOM_CHATS_ENABLED = Boolean(cachedSettings?.random_chats?.enabled);
    const RANDOM_CHAT_IDLE_MS = Number.isFinite(configuredRandomIdleSeconds)
        ? Math.min(3600000, Math.max(30000, Math.round(configuredRandomIdleSeconds * 1000)))
        : 180000;
    const AUTO_SEND_SILENCE_MS = Number.isFinite(configuredAutoSendSilenceMs)
        ? Math.min(5000, Math.max(200, Math.round(configuredAutoSendSilenceMs)))
        : 600;

    let chatRequestInFlight = false;
    let randomChatInFlight = false;
    let randomChatTimer = null;
    let autoSendSpeechTimer = null;
    let wakeWordArmed = false;
    let wakeWordDetectedTimer = null;
    let wakeWordDetectedActive = false;
    let wakeWordLastDetectedMs = 0;
    let wakeWordRealtimeRecognition = null;
    let wakeWordRealtimeActive = false;
    let wakeWordRealtimeRestartTimer = null;

    function clearWakeWordDetectedTimer() {
        if (wakeWordDetectedTimer) {
            clearTimeout(wakeWordDetectedTimer);
            wakeWordDetectedTimer = null;
        }
    }

    function clearWakeWordRealtimeRestartTimer() {
        if (wakeWordRealtimeRestartTimer) {
            clearTimeout(wakeWordRealtimeRestartTimer);
            wakeWordRealtimeRestartTimer = null;
        }
    }

    function setWakeWordIndicator(state, label) {
        if (!wakeWordBadge || !wakeWordLabel) return;

        wakeWordBadge.classList.remove("wake-state-off", "wake-state-on", "wake-state-detected");
        wakeWordBadge.classList.add(state || "wake-state-off");
        wakeWordLabel.textContent = label || "Wake Word: OFF";
    }

    function refreshWakeWordIndicator() {
        if (wakeWordDetectedActive) {
            return;
        }

        if (WAKE_WORD_ENABLED && voiceToggleEnabled) {
            setWakeWordIndicator("wake-state-on", "Wake Word: ON");
            return;
        }

        setWakeWordIndicator("wake-state-off", "Wake Word: OFF");
    }

    function flashWakeWordDetected() {
        if (!WAKE_WORD_ENABLED || !voiceToggleEnabled) {
            return;
        }

        const now = Date.now();
        if (now - wakeWordLastDetectedMs < 700) {
            return;
        }
        wakeWordLastDetectedMs = now;

        wakeWordDetectedActive = true;
        setWakeWordIndicator("wake-state-detected", "Wake Word: DETECTED");
        clearWakeWordDetectedTimer();
        wakeWordDetectedTimer = setTimeout(() => {
            wakeWordDetectedActive = false;
            refreshWakeWordIndicator();
        }, 1300);
    }

    function stopWakeWordRealtimeListener() {
        clearWakeWordRealtimeRestartTimer();
        if (!wakeWordRealtimeRecognition) {
            wakeWordRealtimeActive = false;
            return;
        }

        const recognition = wakeWordRealtimeRecognition;
        wakeWordRealtimeRecognition = null;

        recognition.onresult = null;
        recognition.onerror = null;
        recognition.onend = null;

        if (wakeWordRealtimeActive) {
            try {
                recognition.stop();
            } catch (_error) {
                // Ignore stop errors from already-ended sessions.
            }
        }

        wakeWordRealtimeActive = false;
    }

    function scheduleWakeWordRealtimeRestart() {
        if (!WAKE_WORD_ENABLED || !voiceToggleEnabled || !WakeWordRecognition) {
            return;
        }

        clearWakeWordRealtimeRestartTimer();
        wakeWordRealtimeRestartTimer = setTimeout(() => {
            startWakeWordRealtimeListener();
        }, 180);
    }

    function startWakeWordRealtimeListener() {
        if (!WAKE_WORD_ENABLED || !voiceToggleEnabled || !WakeWordRecognition) {
            return;
        }
        if (wakeWordRealtimeActive) {
            return;
        }

        if (!wakeWordRealtimeRecognition) {
            const recognition = new WakeWordRecognition();
            recognition.continuous = true;
            recognition.interimResults = true;
            recognition.lang = "en-US";

            recognition.onresult = (event) => {
                for (let index = event.resultIndex; index < event.results.length; index += 1) {
                    const result = event.results[index];
                    const transcript = String(result?.[0]?.transcript || "").trim();
                    if (!transcript) {
                        continue;
                    }

                    if (WAKE_WORD_REGEX.test(transcript.toLowerCase())) {
                        flashWakeWordDetected();
                        break;
                    }
                }
            };

            recognition.onerror = () => {
                wakeWordRealtimeActive = false;
                scheduleWakeWordRealtimeRestart();
            };

            recognition.onend = () => {
                wakeWordRealtimeActive = false;
                scheduleWakeWordRealtimeRestart();
            };

            wakeWordRealtimeRecognition = recognition;
        }

        try {
            wakeWordRealtimeRecognition.start();
            wakeWordRealtimeActive = true;
        } catch (_error) {
            wakeWordRealtimeActive = false;
            scheduleWakeWordRealtimeRestart();
        }
    }

    function processTranscriptWithWakeWord(rawTranscript) {
        const transcript = String(rawTranscript || "").trim();
        if (!transcript) return "";
        if (!WAKE_WORD_ENABLED) return transcript;

        // If we already heard the wake word in the previous utterance, consume this one directly.
        if (wakeWordArmed) {
            wakeWordArmed = false;
            return transcript;
        }

        const lower = transcript.toLowerCase();
        const match = WAKE_WORD_REGEX.exec(lower);
        if (!match) {
            return "";
        }

        flashWakeWordDetected();

        const afterWake = transcript.slice(match.index + match[0].length).replace(/^[\s,.:;!?-]+/, "").trim();

        // If user said only wake word, arm the next utterance.
        if (!afterWake) {
            wakeWordArmed = true;
            return "";
        }

        return afterWake;
    }

    function setToggleState(toggleEl, enabled, disabled = false) {
        if (!toggleEl) return;
        toggleEl.classList.toggle("is-on", Boolean(enabled));
        toggleEl.setAttribute("aria-checked", enabled ? "true" : "false");
        toggleEl.disabled = Boolean(disabled);
    }

    function refreshVoiceUi() {
        setToggleState(transcribeSpeechToggle, voiceToggleEnabled, sttBusy);
        refreshWakeWordIndicator();
    }

    function refreshTtsUi() {
        setToggleState(speakRepliesToggle, speakRepliesEnabled, false);
        if (stopTtsBtn) {
            // Keep stop enabled so users can interrupt tool-initiated playback too.
            stopTtsBtn.disabled = false;
        }
        refreshVoiceUi();
    }

    let toolIndicatorResetTimer = null;
    function setToolIndicator(state, label) {
        if (!toolActivityBadge || !toolActivityLabel) return;

        toolActivityBadge.classList.remove("tool-state-idle", "tool-state-working", "tool-state-used", "tool-state-error");
        toolActivityBadge.classList.add(state || "tool-state-idle");
        toolActivityLabel.textContent = label || "Tools idle";

        if (toolIndicatorResetTimer) {
            clearTimeout(toolIndicatorResetTimer);
            toolIndicatorResetTimer = null;
        }

        if (state === "tool-state-used" || state === "tool-state-error") {
            toolIndicatorResetTimer = setTimeout(() => {
                setToolIndicator("tool-state-idle", "Tools idle");
            }, 2600);
        }
    }

    function clearRandomChatTimer() {
        if (randomChatTimer) {
            clearTimeout(randomChatTimer);
            randomChatTimer = null;
        }
    }

    function clearAutoSendSpeechTimer() {
        if (autoSendSpeechTimer) {
            clearTimeout(autoSendSpeechTimer);
            autoSendSpeechTimer = null;
        }
    }

    function scheduleAutoSendFromSpeech() {
        if (!AUTO_SEND_ON_SILENCE || !WAKE_WORD_ENABLED || !voiceToggleEnabled) {
            return;
        }

        const candidate = String(chatInput?.value || "").trim();
        if (!candidate) {
            return;
        }

        clearAutoSendSpeechTimer();
        autoSendSpeechTimer = setTimeout(() => {
            if (!voiceToggleEnabled || chatRequestInFlight || sttBusy || sttRecording) {
                return;
            }

            const latest = String(chatInput?.value || "").trim();
            if (!latest || latest !== candidate) {
                return;
            }

            chatForm.requestSubmit();
        }, AUTO_SEND_SILENCE_MS);
    }

    async function maybeSendRandomChat() {
        if (!RANDOM_CHATS_ENABLED || randomChatInFlight || chatRequestInFlight) {
            return;
        }
        if (ttsBusy || sttBusy || sttRecording) {
            return;
        }
        if (document.hidden) {
            return;
        }
        if (chatInput?.value?.trim()) {
            return;
        }

        randomChatInFlight = true;
        try {
            const response = await fetch("/api/random-chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ history: currentMessages }),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || "Random chat request failed");
            }

            const reply = String(data?.reply || "").trim();
            if (!reply) {
                return;
            }

            lastAssistantReply = reply;
            const createdAt = new Date().toISOString();
            currentMessages.push({ role: "assistant", content: reply, created_at: createdAt });
            appendMessage("assistant", reply, createdAt);

            if (speakRepliesEnabled) {
                await triggerSpeakLastReply(false);
            }
        } catch (error) {
            console.warn("Random chat generation failed", error);
        } finally {
            randomChatInFlight = false;
            scheduleRandomChat();
        }
    }

    function scheduleRandomChat() {
        if (!RANDOM_CHATS_ENABLED) {
            return;
        }
        clearRandomChatTimer();
        randomChatTimer = setTimeout(() => {
            maybeSendRandomChat();
        }, RANDOM_CHAT_IDLE_MS);
    }

    function stopVoiceMonitoring() {
        if (sttMonitorInterval) {
            clearInterval(sttMonitorInterval);
            sttMonitorInterval = null;
        }
    }

    async function teardownVoiceEngine() {
        stopVoiceMonitoring();

        if (sttSourceNode) {
            sttSourceNode.disconnect();
            sttSourceNode = null;
        }
        if (sttAnalyser) {
            sttAnalyser.disconnect();
            sttAnalyser = null;
        }
        if (sttAudioContext) {
            await sttAudioContext.close();
            sttAudioContext = null;
        }

        stopRecordingStream();
    }

    async function ensureVoiceEngine() {
        if (sttStream && sttAnalyser) {
            return;
        }

        sttStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            },
        });

        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) {
            throw new Error("This browser does not support live voice detection.");
        }

        sttAudioContext = new AudioCtx();
        sttSourceNode = sttAudioContext.createMediaStreamSource(sttStream);
        sttAnalyser = sttAudioContext.createAnalyser();
        sttAnalyser.fftSize = 1024;
        sttAnalyser.smoothingTimeConstant = 0.25;
        sttSourceNode.connect(sttAnalyser);
    }

    function getCurrentAudioLevel() {
        if (!sttAnalyser) return 0;

        const data = new Float32Array(sttAnalyser.fftSize);
        sttAnalyser.getFloatTimeDomainData(data);

        let sum = 0;
        for (let i = 0; i < data.length; i += 1) {
            sum += data[i] * data[i];
        }
        return Math.sqrt(sum / data.length);
    }

    async function startUtteranceRecording() {
        if (!voiceToggleEnabled || sttBusy || sttRecording || !sttStream || ttsBusy) {
            return;
        }

        const mimeType = chooseRecordingMimeType();
        sttRecorder = mimeType ? new MediaRecorder(sttStream, { mimeType }) : new MediaRecorder(sttStream);
        sttChunks = [];
        sttSpeechStartedMs = Date.now();
        sttLastSpeechMs = sttSpeechStartedMs;

        sttRecorder.addEventListener("dataavailable", (event) => {
            if (event.data && event.data.size > 0) {
                sttChunks.push(event.data);
            }
        });

        sttRecorder.addEventListener("error", () => {
            sttRecording = false;
            sttBusy = false;
            sttRecorder = null;
            sttChunks = [];
            refreshVoiceUi();
            alert("Microphone recording error occurred.");
        });

        sttRecorder.start(250);
        sttRecording = true;
        refreshVoiceUi();
    }

    async function stopUtteranceRecordingAndMaybeTranscribe(shouldTranscribe) {
        if (!sttRecorder || !sttRecording) {
            return;
        }

        sttBusy = true;
        sttRecording = false;
        refreshVoiceUi();

        await new Promise((resolve) => {
            const recorderRef = sttRecorder;
            sttRecorder = null;

            recorderRef.addEventListener(
                "stop",
                async () => {
                    try {
                        const mimeType = recorderRef.mimeType || "audio/webm";
                        const blob = new Blob(sttChunks, { type: mimeType });

                        if (shouldTranscribe && blob.size) {
                            const ext = mimeType.includes("ogg") ? "ogg" : mimeType.includes("mp4") ? "m4a" : "webm";
                            const file = new File([blob], `voice-input.${ext}`, { type: mimeType });
                            let transcript = await transcribeAudio(file);

                            if ((!transcript || !transcript.trim()) && mimeType !== "audio/wav") {
                                try {
                                    const wavBlob = await convertBlobToWav(blob);
                                    const wavFile = new File([wavBlob], "voice-input.wav", { type: "audio/wav" });
                                    transcript = await transcribeAudio(wavFile);
                                } catch (conversionError) {
                                    console.warn("WAV fallback conversion failed", conversionError);
                                }
                            }

                            const gatedTranscript = processTranscriptWithWakeWord(transcript);
                            if (gatedTranscript && gatedTranscript.trim()) {
                                chatInput.value = gatedTranscript.trim();
                                chatInput.focus();
                                scheduleAutoSendFromSpeech();
                            }
                        }
                    } catch (error) {
                        alert(error.message || "Voice input failed");
                    } finally {
                        sttChunks = [];
                        sttBusy = false;
                        refreshVoiceUi();
                        resolve();
                    }
                },
                { once: true }
            );

            recorderRef.stop();
        });
    }

    function startVoiceMonitoring() {
        stopVoiceMonitoring();

        sttMonitorInterval = setInterval(async () => {
            if (!voiceToggleEnabled || sttBusy || !sttAnalyser) {
                return;
            }

            if (ttsBusy) {
                if (sttRecording) {
                    await stopUtteranceRecordingAndMaybeTranscribe(false);
                }
                refreshVoiceUi();
                return;
            }

            const level = getCurrentAudioLevel();
            const now = Date.now();

            if (level > VAD_THRESHOLD) {
                sttLastSpeechMs = now;
                if (!sttRecording) {
                    await startUtteranceRecording();
                }
                return;
            }

            if (!sttRecording) {
                return;
            }

            const silenceElapsed = now - sttLastSpeechMs;
            if (silenceElapsed < VAD_SILENCE_MS) {
                return;
            }

            const speechDuration = now - sttSpeechStartedMs;
            const shouldTranscribe = speechDuration >= VAD_MIN_SPEECH_MS;
            await stopUtteranceRecordingAndMaybeTranscribe(shouldTranscribe);
        }, VAD_INTERVAL_MS);
    }

    async function setVoiceToggleEnabled(enabled, showErrors = true) {
        if (enabled === voiceToggleEnabled) {
            return;
        }

        if (enabled) {
            if (!cachedSettings?.stt?.enabled) {
                if (showErrors) {
                    alert("Speech input is disabled. Enable it in Configuration.");
                }
                return;
            }
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
                if (showErrors) {
                    alert("This browser does not support in-page microphone recording.");
                }
                return;
            }

            try {
                await ensureVoiceEngine();
                voiceToggleEnabled = true;
                startVoiceMonitoring();
                startWakeWordRealtimeListener();
                refreshVoiceUi();
            } catch (error) {
                const message =
                    error?.name === "NotAllowedError"
                        ? "Microphone permission was denied. Allow access and try again."
                        : error?.message || "Unable to start voice input.";
                if (showErrors) {
                    alert(message);
                }
                await teardownVoiceEngine();
                voiceToggleEnabled = false;
                refreshVoiceUi();
            }
            return;
        }

        voiceToggleEnabled = false;
        wakeWordArmed = false;
        wakeWordDetectedActive = false;
        clearWakeWordDetectedTimer();
        clearAutoSendSpeechTimer();
        stopWakeWordRealtimeListener();
        if (sttRecording) {
            await stopUtteranceRecordingAndMaybeTranscribe(false);
        }
        await teardownVoiceEngine();
        refreshVoiceUi();
    }

    function abortTtsPlayback() {
        if (!ttsBusy) {
            return;
        }

        ttsAbortRequested = true;
        if (ttsFetchController) {
            ttsFetchController.abort();
            ttsFetchController = null;
        }

        const audio = getTtsAudioElement();
        if (!audio.paused) {
            audio.pause();
        }
        audio.currentTime = 0;
    }

    async function waitForTtsIdle(maxWaitMs = 4000) {
        const started = Date.now();
        while (ttsBusy && Date.now() - started < maxWaitMs) {
            await new Promise((resolve) => setTimeout(resolve, 40));
        }
    }

    async function triggerSpeakText(text, showAlerts = true, interruptIfBusy = false) {
        const speechText = String(text || "").trim();
        if (!cachedSettings?.tts?.enabled) {
            if (showAlerts) {
                alert("Text to speech is disabled. Enable it in Configuration.");
            }
            return;
        }
        if (!speechText) {
            if (showAlerts) {
                alert("There is no reply text to speak.");
            }
            return;
        }

        if (ttsBusy) {
            if (!interruptIfBusy) {
                return;
            }

            abortTtsPlayback();
            await waitForTtsIdle();
            if (ttsBusy) {
                return;
            }
        }

        if (sttRecording) {
            await stopUtteranceRecordingAndMaybeTranscribe(false);
        }

        ttsBusy = true;
        ttsAbortRequested = false;
        refreshTtsUi();

        try {
            await speakText(speechText);
        } catch (error) {
            if ((ttsAbortRequested || error?.name === "AbortError") && showAlerts) {
                // Silent user-initiated abort.
            } else if (showAlerts) {
                alert(error.message || "Text to speech failed");
            } else {
                console.warn("Auto-speak failed", error);
            }
        } finally {
            ttsBusy = false;
            ttsAbortRequested = false;
            refreshTtsUi();
        }
    }

    async function triggerSpeakLastReply(showAlerts = true) {
        if (!lastAssistantReply) {
            if (showAlerts) {
                alert("No assistant reply is available to speak yet.");
            }
            return;
        }
        await triggerSpeakText(lastAssistantReply, showAlerts);
    }

    speakReplyFromBubble = async (text) => {
        await triggerSpeakText(text, true, true);
    };

    chatInput.addEventListener("keydown", (event) => {
        clearAutoSendSpeechTimer();
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            chatForm.requestSubmit();
        }
    });

    chatInput.addEventListener("input", () => {
        clearAutoSendSpeechTimer();
    });

    chatForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = chatInput.value.trim();
        if (!message) return;

        clearAutoSendSpeechTimer();
        clearRandomChatTimer();
        chatInput.value = "";
        const userCreatedAt = new Date().toISOString();
        currentMessages.push({ role: "user", content: message, created_at: userCreatedAt });
        appendMessage("user", message, userCreatedAt);
        setToolIndicator("tool-state-working", "Checking tools...");
        chatRequestInFlight = true;

        try {
            const chatResult = await sendChat(message);
            const reply = String(chatResult?.reply || "");
            const toolDebug = chatResult?.tool_debug || {};
            const toolEvents = Array.isArray(toolDebug?.events) ? toolDebug.events : [];
            const usedTools = [...new Set(toolEvents.map((event) => event?.tool).filter(Boolean))];

            if (toolDebug?.used && usedTools.length) {
                const first = usedTools[0];
                const label = usedTools.length > 1 ? `Tools used: ${first} +${usedTools.length - 1}` : `Tool used: ${first}`;
                setToolIndicator("tool-state-used", label);
            } else {
                setToolIndicator("tool-state-idle", "Tools idle");
            }

            lastAssistantReply = reply;
            const assistantCreatedAt = new Date().toISOString();
            currentMessages.push({ role: "assistant", content: reply, created_at: assistantCreatedAt });
            appendMessage("assistant", reply, assistantCreatedAt);

            if (speakRepliesEnabled) {
                await triggerSpeakLastReply(false);
            }
        } catch (error) {
            const errMsg = `Error: ${error.message}`;
            const errorCreatedAt = new Date().toISOString();
            currentMessages.push({ role: "assistant", content: errMsg, created_at: errorCreatedAt });
            appendMessage("assistant", errMsg, errorCreatedAt);
            setToolIndicator("tool-state-error", "Tool check failed");
        } finally {
            chatRequestInFlight = false;
            scheduleRandomChat();
        }
    });

    saveChatBtn?.addEventListener("click", async () => {
        await saveCurrentChat();
    });

    newChatBtn?.addEventListener("click", () => {
        clearAutoSendSpeechTimer();
        loadedChatId = null;
        currentMessages = [];
        lastAssistantReply = "";
        renderHistory(currentMessages);
        scheduleRandomChat();
    });

    transcribeSpeechToggle?.addEventListener("click", async () => {
        await setVoiceToggleEnabled(!voiceToggleEnabled, true);
    });

    speakRepliesToggle?.addEventListener("click", () => {
        speakRepliesEnabled = !speakRepliesEnabled;
        refreshTtsUi();
    });

    stopTtsBtn?.addEventListener("click", async () => {
        abortTtsPlayback();
        try {
            await fetch("/api/audio/stop", { method: "POST" });
        } catch (error) {
            console.warn("Backend audio stop failed", error);
        }
    });

    const randomActivityEvents = ["mousemove", "keydown", "mousedown", "scroll", "touchstart"];
    randomActivityEvents.forEach((eventName) => {
        window.addEventListener(eventName, scheduleRandomChat, { passive: true });
    });
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            clearRandomChatTimer();
            return;
        }
        scheduleRandomChat();
    });

    // Initialize toggles from configured defaults.
    speakRepliesEnabled = Boolean(cachedSettings?.tts?.auto_speak_default);
    if (cachedSettings?.stt?.transcribe_speech_default) {
        await setVoiceToggleEnabled(true, false);
    }

    refreshVoiceUi();
    refreshTtsUi();
    scheduleRandomChat();
}

document.addEventListener("DOMContentLoaded", async () => {
    document.addEventListener("click", () => {
        closeChatMenu();
    });
    window.addEventListener("resize", () => {
        closeChatMenu();
    });
    window.addEventListener(
        "scroll",
        () => {
            closeChatMenu();
        },
        true
    );

    setupThemeToggle();
    try {
        await initChatPage();
    } catch (error) {
        console.error("Initialization error", error);
    }
});
