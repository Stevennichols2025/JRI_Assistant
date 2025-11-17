// Chat application JavaScript

// Auto-resize textarea
function autoResizeTextarea(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = textarea.scrollHeight + 'px';
}

// Add message to chat
function addMessage(message, isUser, timestamp = null) {
    const chatHistory = document.getElementById('chat-history');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${isUser ? 'user-message' : 'bot-message'}`;
    
    // Add timestamp if provided
    if (timestamp) {
        const timeSpan = document.createElement('span');
        timeSpan.className = 'message-timestamp';
        timeSpan.textContent = timestamp;
        timeSpan.style.fontSize = '0.75rem';
        timeSpan.style.color = 'var(--text-secondary)';
        timeSpan.style.display = 'block';
        timeSpan.style.marginBottom = '0.25rem';
        messageDiv.appendChild(timeSpan);
    }
    
    // Add message content
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.textContent = message;
    messageDiv.appendChild(contentDiv);
    
    chatHistory.appendChild(messageDiv);
    scrollToBottom();
    
    return messageDiv;
}

// Scroll to bottom of chat
function scrollToBottom() {
    const chatHistory = document.getElementById('chat-history');
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

// Get current timestamp
function getTimestamp() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// Send message function
function sendMessage() {
    const input = document.getElementById('user-input');
    const sendButton = document.getElementById('send-button');
    const message = input.value.trim();
    const selectedModel = document.getElementById('model').value;
    
    if (!message) return;
    
    // Disable input and button
    input.disabled = true;
    sendButton.disabled = true;
    
    // Add user message
    addMessage(message, true, getTimestamp());
    input.value = '';
    autoResizeTextarea(input);
    
    // Show loading indicator
    const loadingDiv = addMessage('...', false);
    loadingDiv.classList.add('loading-message');
    
    fetch('/send_message', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ 
            message: message,
            model: selectedModel
        })
    })
    .then(response => response.json())
    .then(data => {
        // Remove loading indicator
        loadingDiv.remove();
        
        if (data.error) {
            addMessage('Error: ' + data.error, false, getTimestamp());
        } else {
            addMessage(data.response, false, getTimestamp());
        }
    })
    .catch(error => {
        loadingDiv.remove();
        addMessage('Error: ' + error, false, getTimestamp());
    })
    .finally(() => {
        // Re-enable input and button
        input.disabled = false;
        sendButton.disabled = false;
        input.focus();
    });
}

// Clear chat history
function clearChat() {
    if (confirm('Are you sure you want to clear the chat history?')) {
        const chatHistory = document.getElementById('chat-history');
        chatHistory.innerHTML = '';
        
        fetch('/clear_history', {
            method: 'POST'
        })
        .then(response => response.json())
        .then(data => {
            console.log('History cleared');
        })
        .catch(error => {
            console.error('Error clearing history:', error);
        });
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    const input = document.getElementById('user-input');
    const sendButton = document.getElementById('send-button');
    
    if (input) {
        // Auto-resize textarea
        input.addEventListener('input', function() {
            autoResizeTextarea(this);
        });
        
        // Keyboard shortcuts: Enter to send, Shift+Enter for new line
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (typeof sendMessage === 'function') {
                    sendMessage();
                }
            }
        });
        
        // Focus input on load
        input.focus();
    }
});

