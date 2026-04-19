// 예시 질문 설정
function setQuery(query) {
    document.getElementById('queryInput').value = query;
    document.getElementById('queryInput').focus();
}

// 채팅 기록 삭제
async function clearChat() {
    if (confirm('채팅 기록을 모두 삭제하시겠습니까?')) {
        try {
            await fetch('/clear-chat', { method: 'POST' });
            location.reload();
        } catch (error) {
            alert('채팅 기록 삭제에 실패했습니다.');
        }
    }
}

const chatMessages = document.getElementById('chatMessages');

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function appendUserMessage(content) {
    const html = `
        <div class="d-flex mb-3 justify-content-end">
            <div class="bg-primary text-white rounded p-3 shadow-sm">
                <p class="mb-1">${content}</p>
                <small class="text-light">사용자</small>
            </div>
            <div class="bg-primary rounded-circle p-2 ms-2">
                <i class="bi bi-person-fill text-white"></i>
            </div>
        </div>
    `;
    chatMessages.insertAdjacentHTML('beforeend', html);
    scrollToBottom();
}

function createAIMessageBlock(id) {
    const html = `
        <div class="d-flex mb-3">
            <div class="bg-success rounded-circle p-2 me-2" style="height:fit-content;">
                <i class="bi bi-robot text-white"></i>
            </div>
            <div class="bg-white rounded p-3 shadow-sm w-100">
                <div id="${id}"></div>
                <small class="text-muted d-block mt-2">AI</small>
            </div>
        </div>
    `;
    chatMessages.insertAdjacentHTML('beforeend', html);
    scrollToBottom();
    return document.getElementById(id);
}

// 폼 제출
document.getElementById('chatForm').addEventListener('submit', function(e) {
    e.preventDefault();

    const button = document.getElementById('sendButton');
    const input = document.getElementById('queryInput');
    const query = input.value;

    if (!query.trim()) return;

    appendUserMessage(query);

    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>응답 중...';
    input.value = '';

    const aiMsgElId = 'ai-msg-' + Date.now();
    const aiMsgEl = createAIMessageBlock(aiMsgElId);

    // 스트리밍 시작
    const source = new EventSource('/chat/stream?query=' + encodeURIComponent(query));
    source.onmessage = function(event) {
        const data = JSON.parse(event.data);
        if (data.type === 'token') {
            let span = document.createElement("span");
            span.innerText = data.content;
            aiMsgEl.appendChild(span);
            scrollToBottom();
        } else if (data.type === 'tool_start') {
            aiMsgEl.insertAdjacentHTML('beforeend', `<div class="badge bg-secondary mb-2 d-inline-block me-1">${data.content}</div>`);
            scrollToBottom();
        } else if (data.type === 'tool_end') {
            // 도구 완료 표시 생략
        } else if (data.type === 'finish') {
            source.close();
            button.disabled = false;
            button.innerHTML = '<i class="bi bi-send"></i> 전송';
            input.focus();
        } else if (data.type === 'error') {
            source.close();
            aiMsgEl.insertAdjacentHTML('beforeend', `<br><span class="text-danger">오류 발생: ${data.content}</span>`);
            button.disabled = false;
            button.innerHTML = '<i class="bi bi-send"></i> 전송';
        }
    };

    source.onerror = function() {
        source.close();
        aiMsgEl.insertAdjacentHTML('beforeend', '<br><span class="text-danger">스트리밍 연결 오류 발생</span>');
        button.disabled = false;
        button.innerHTML = '<i class="bi bi-send"></i> 전송';
    };
});

// 페이지 로드 시 설정
document.addEventListener('DOMContentLoaded', function() {
    scrollToBottom();
    document.getElementById('queryInput').focus();
});
