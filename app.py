from flask import Flask, render_template, request, jsonify, session, Response, stream_with_context
from openai import OpenAI, APIError, APIConnectionError, RateLimitError
import os
import secrets
import json
import re
from dotenv import load_dotenv
from datetime import datetime
from pathlib import Path

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(16))

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
LOCAL_BASE_URL = os.getenv('LOCAL_BASE_URL', 'http://localhost:1234/v1')

# Validate required configuration
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is required")

# Configure OpenAI client for official models
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Configure client for local models
local_client = OpenAI(
    base_url=LOCAL_BASE_URL,
    api_key="not-needed"  # LM Studio doesn't require an API key
)

# Define which models use which endpoint
LOCAL_MODELS = {
    'deepseek-r1-distill-qwen-7b': local_client,
    'qwen2.5-7b-instruct-1m': local_client
}
 
OPENAI_MODELS = {
    # GPT-3.5 Series (Legacy)
    'gpt-3.5-turbo': openai_client,
    
    # GPT-4.1 Series (Multimodal LLM - Most Versatile)
    'gpt-4.1': openai_client,
    'gpt-4o': openai_client,
    'gpt-4o-mini': openai_client,
    'gpt-4-turbo': openai_client,
    
    # GPT-5 Series (Reasoning Models - Latest)
    'gpt-5-mini': openai_client,
    'gpt-5-nano': openai_client,
    'gpt-5-chat': openai_client,
    
    # O-Series (Original reasoning models)
    'o1': openai_client,
    'o1-mini': openai_client,
    
    # Deep Research Models
    'o3-deep-research-2025-06-26': openai_client,
    'o4-mini-deep-research-2025-06-26': openai_client,
    
    # Vision/Specialized
    'gpt-4-vision-preview': openai_client,
}

# Combine all available models for easy lookup
ALL_MODELS = {**LOCAL_MODELS, **OPENAI_MODELS}

# Load system prompt (JRI protocol + Quality Matrix)
def load_system_prompt():
    """Load and combine JRI protocol and quality matrix into system prompt"""
    # Load JRI protocol
    jri_text = None
    try:
        with open('jri.tex', 'r', encoding='utf-8') as f:
            jri_text = f.read()
    except FileNotFoundError:
        raise FileNotFoundError("jri.tex file not found in the project root")
    except Exception as e:
        raise Exception(f"Error loading jri.tex: {str(e)}")
    
    # Load quality matrix
    quality_json = None
    try:
        with open('quality_matrix.json', 'r', encoding='utf-8') as f:
            quality_json = json.load(f)
    except FileNotFoundError:
        # If quality matrix doesn't exist, use empty structure
        quality_json = {"conversational_modes": {}, "total_qualities": 0, "total_modes": 0, "qualities_per_mode": 0}
        print("Warning: quality_matrix.json not found, using empty quality matrix")
    except json.JSONDecodeError as e:
        raise Exception(f"Error parsing quality_matrix.json: {str(e)}")
    except Exception as e:
        raise Exception(f"Error loading quality_matrix.json: {str(e)}")
    
    # Combine into system prompt
    system_prompt = f"""{jri_text}

---

QUALITY MATRIX INTEGRATION:

{json.dumps(quality_json, indent=2)}

CRITICAL INSTRUCTION: Apply both CFP recursion AND quality-mode adaptation to every response. Detect user's quality-state from their message, select appropriate mode, then recurse within that mode's coherence standards before responding."""

    return system_prompt

# Load system prompt at startup
SYSTEM_PROMPT = load_system_prompt()

# Create chats directory if it doesn't exist
CHATS_DIR = Path('chats')
CHATS_DIR.mkdir(exist_ok=True)

def get_chat_filename(chat_id):
    """Get the filename for a chat session"""
    return CHATS_DIR / f"{chat_id}.json"

def generate_chat_id():
    """Generate a unique chat ID"""
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(4)

def get_chat_title(messages):
    """Generate a title from the first user message - use first two sentences concatenated"""
    if messages and len(messages) > 0:
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                if not content:
                    continue
                
                # Split into sentences (simple approach: split on . ! ?)
                sentences = re.split(r'[.!?]+', content)
                # Filter out empty strings and get first two sentences
                sentences = [s.strip() for s in sentences if s.strip()]
                
                if len(sentences) >= 2:
                    # Concatenate first two sentences
                    title = sentences[0] + '. ' + sentences[1] + '.'
                elif len(sentences) == 1:
                    # Only one sentence, use it
                    title = sentences[0] + '.'
                else:
                    # No sentences found, use first 100 chars
                    title = content[:100]
                
                # Limit to reasonable length (150 chars max)
                if len(title) > 150:
                    title = title[:147] + '...'
                
                return title
    return 'New Chat'

@app.route('/')
def home():
    # Initialize empty conversation history when starting new chat
    # This ensures a fresh page load always starts with an empty session
    # Only loaded chats (via /load_chat) will populate the session
    # Force clear all session data to ensure clean state
    session.clear()
    session['messages'] = []
    session['current_chat_id'] = None  # Track if we're in a loaded chat
    session['saved_chat_id'] = None  # Track if current chat has been saved (prevents duplicates)
    return render_template('index.html')

@app.route('/send_message', methods=['POST'])
def send_message():
    # Validate request data
    if not request.json:
        return jsonify({"error": "Request must be JSON"}), 400
    
    user_message = request.json.get('message')
    selected_model = request.json.get('model')
    
    if not user_message:
        return jsonify({"error": "Message is required"}), 400
    
    if not selected_model:
        return jsonify({"error": "Model is required"}), 400
    
    # Get conversation history from session or initialize if not exists
    messages = session.get('messages', [])
    
    # Build messages array with system prompt (JRI + Quality Matrix), then conversation history
    api_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    api_messages.extend(messages)
    
    # Add user message to history
    api_messages.append({"role": "user", "content": user_message})
    
    try:
        # Select appropriate client based on model
        client = ALL_MODELS.get(selected_model)
        if not client:
            return jsonify({"error": f"Unknown model: {selected_model}"}), 400

        # Call API with jri.tex + conversation history + current message
        response = client.chat.completions.create(
            model=selected_model,
            messages=api_messages
        )
        
        bot_response = response.choices[0].message.content
        
        # Add user message and bot response to session history (without jri.tex)
        messages.append({"role": "user", "content": user_message})
        messages.append({"role": "assistant", "content": bot_response})
        
        # Save updated history back to session
        session['messages'] = messages
        
        return jsonify({"response": bot_response})
    
    except RateLimitError as e:
        return jsonify({"error": f"Rate limit exceeded: {str(e)}"}), 429
    except APIConnectionError as e:
        return jsonify({"error": f"Connection error: {str(e)}"}), 503
    except APIError as e:
        return jsonify({"error": f"API error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@app.route('/clear_history', methods=['POST'])
def clear_history():
    """Clear conversation history for current session"""
    session['messages'] = []
    session['current_chat_id'] = None  # Mark as new chat, not a loaded one
    session['saved_chat_id'] = None  # Reset saved flag for new chat
    return jsonify({"status": "History cleared"})

@app.route('/get_history', methods=['GET'])
def get_history():
    """Get conversation history for current session"""
    messages = session.get('messages', [])
    return jsonify({"messages": messages})

@app.route('/stream_message', methods=['POST'])
def stream_message():
    """Stream message response using Server-Sent Events"""
    # Validate request data
    if not request.json:
        return jsonify({"error": "Request must be JSON"}), 400
    
    user_message = request.json.get('message')
    selected_model = request.json.get('model')
    
    if not user_message:
        return jsonify({"error": "Message is required"}), 400
    
    if not selected_model:
        return jsonify({"error": "Model is required"}), 400
    
    # Get conversation history from session or initialize if not exists
    # If current_chat_id is None, this is a fresh new chat - ensure session is clean
    messages = session.get('messages', [])
    if session.get('current_chat_id') is None and len(messages) > 0:
        # Fresh new chat - clear session to start fresh
        messages = []
        session['messages'] = []
    
    # Validate message has meaningful content (at least 3 characters after trimming)
    # This prevents empty or whitespace-only messages from being processed
    if not user_message or len(user_message.strip()) < 3:
        # Return error as streaming response
        def error_response():
            yield f"data: {json.dumps({'error': 'Message must contain at least 3 characters'})}\n\n"
        return Response(stream_with_context(error_response()), mimetype='text/event-stream')
    
    # Add user message to session history BEFORE streaming (so it's saved immediately)
    # This ensures the session has the actual user message content from the user
    messages.append({"role": "user", "content": user_message})
    session['messages'] = messages  # Save to session immediately
    
    # Build messages array with system prompt (JRI + Quality Matrix), then conversation history
    api_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    api_messages.extend(messages)  # messages now includes the user message
    
    def generate():
        try:
            # Select appropriate client based on model
            client = ALL_MODELS.get(selected_model)
            if not client:
                yield f"data: {json.dumps({'error': f'Unknown model: {selected_model}'})}\n\n"
                return

            # Call API with streaming enabled
            stream = client.chat.completions.create(
                model=selected_model,
                messages=api_messages,
                stream=True
            )
            
            full_response = ""
            for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    content = chunk.choices[0].delta.content
                    full_response += content
                    # Send chunk as SSE
                    yield f"data: {json.dumps({'chunk': content})}\n\n"
            
            # CRITICAL: Add bot response to session history BEFORE sending done signal
            # This ensures the session is updated before the frontend can trigger a save
            # Get fresh copy of messages from session to ensure we have the latest
            updated_messages = session.get('messages', [])
            updated_messages.append({"role": "assistant", "content": full_response})
            
            # Save updated history back to session IMMEDIATELY
            session['messages'] = updated_messages
            
            # Log the complete conversation state after assistant response
            print("=" * 60)
            print("[STREAM_MESSAGE] === ASSISTANT RESPONSE COMPLETE ===")
            print(f"[STREAM_MESSAGE] Total messages in session: {len(updated_messages)}")
            for idx, msg in enumerate(updated_messages, 1):
                print(f"[STREAM_MESSAGE] Message {idx}: role={msg.get('role')}, content_length={len(msg.get('content', ''))}")
                if msg.get('content'):
                    preview = msg.get('content', '')[:80].replace('\n', ' ')
                    print(f"[STREAM_MESSAGE]   Preview: {preview}...")
            print("=" * 60)
            
            # Now send completion signal (session is already updated)
            yield f"data: {json.dumps({'done': True})}\n\n"
            
        except RateLimitError as e:
            yield f"data: {json.dumps({'error': f'Rate limit exceeded: {str(e)}'})}\n\n"
        except APIConnectionError as e:
            yield f"data: {json.dumps({'error': f'Connection error: {str(e)}'})}\n\n"
        except APIError as e:
            yield f"data: {json.dumps({'error': f'API error: {str(e)}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'Unexpected error: {str(e)}'})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/save_chat', methods=['POST'])
def save_chat():
    """Save current chat session
    
    FLOW DOCUMENTATION:
    1. Get session messages (source of truth - contains exact sent/received messages)
    2. Get provided messages from request (DOM-extracted, fallback)
    3. Use whichever has MORE messages (most complete conversation)
    4. Validate messages have content
    5. Save to file with all messages exactly as they are
    6. Mark as saved to prevent duplicates
    
    This ensures the saved chat contains EXACTLY what was sent and received in that session.
    """
    print("=" * 60)
    print("[SAVE_CHAT] === SAVE_CHAT ENDPOINT CALLED ===")
    print(f"[SAVE_CHAT] Request method: {request.method}")
    print(f"[SAVE_CHAT] Content-Type: {request.content_type}")
    print(f"[SAVE_CHAT] Request has JSON: {request.json is not None}")
    print("[SAVE_CHAT] STEP 1: Getting session messages (source of truth)")
    
    try:
        # Check if this chat has already been saved - if so, UPDATE it instead of creating new
        saved_chat_id = session.get('saved_chat_id', None)
        current_chat_id = session.get('current_chat_id', None)
        
        # Determine which chat_id to use for update (prefer current_chat_id if loaded, otherwise saved_chat_id)
        existing_chat_id = current_chat_id if current_chat_id else saved_chat_id
        
        update_existing = False
        if existing_chat_id:
            # Check if the file exists
            existing_filename = get_chat_filename(existing_chat_id)
            if existing_filename.exists():
                update_existing = True
                print(f"[SAVE_CHAT] Chat already exists with ID {existing_chat_id}, will UPDATE instead of creating new")
            else:
                print(f"[SAVE_CHAT] Chat ID {existing_chat_id} in session but file doesn't exist, will create new")
                existing_chat_id = None
        
        messages = None
        
        # STEP 1: Get session messages first (source of truth - contains exact sent/received messages)
        session_messages = session.get('messages', [])
        print(f"[SAVE_CHAT] Session messages count: {len(session_messages)}")
        if session_messages:
            print("[SAVE_CHAT] Session messages (BEFORE save):")
            for idx, msg in enumerate(session_messages, 1):
                print(f"[SAVE_CHAT]   {idx}. role={msg.get('role')}, content_length={len(msg.get('content', ''))}")
                if msg.get('content'):
                    preview = msg.get('content', '')[:100].replace('\n', ' ')
                    print(f"[SAVE_CHAT]      Preview: {preview}...")
        
        # Handle regular JSON requests (only way chats are saved - via "New Chat" button)
        if request.json:
            print("[SAVE_CHAT] Detected regular JSON request")
            provided_messages = request.json.get('messages', [])
            chat_history_html = request.json.get('chat_history_html', '')
            
            print(f"[SAVE_CHAT] Request provided {len(provided_messages)} messages")
            print(f"[SAVE_CHAT] Request provided chat_history_html length: {len(chat_history_html)}")
            print(f"[SAVE_CHAT] Session has {len(session_messages)} messages")
            
            # STEP 2: Use whichever has MORE messages (most complete conversation)
            # This ensures we save the full conversation even if session is slightly behind
            print("[SAVE_CHAT] STEP 2: Comparing session vs provided messages")
            if len(session_messages) >= len(provided_messages):
                messages = session_messages
                print(f"[SAVE_CHAT] Using session messages ({len(session_messages)} messages) - more complete")
                print("[SAVE_CHAT] Session messages will be saved (source of truth)")
                session['messages'] = session_messages
            else:
                messages = provided_messages
                print(f"[SAVE_CHAT] Using provided messages ({len(provided_messages)} messages) - more complete than session")
                print("[SAVE_CHAT] Provided messages will be saved (updating session with complete set)")
                # Update session with the more complete set
                session['messages'] = provided_messages
        else:
            # No request body, use session (source of truth)
            print("[SAVE_CHAT] No request body, using session messages")
            messages = session_messages
        
        # STEP 3: Final message set to save
        print(f"[SAVE_CHAT] STEP 3: Final messages to save: {len(messages)}")
        print("[SAVE_CHAT] Complete conversation to be saved:")
        for idx, msg in enumerate(messages, 1):
            print(f"[SAVE_CHAT]   {idx}. role={msg.get('role')}, content_length={len(msg.get('content', ''))}")
            if msg.get('content'):
                preview = msg.get('content', '')[:100].replace('\n', ' ')
                print(f"[SAVE_CHAT]      Preview: {preview}...")
        
        if not messages:
            print("[SAVE_CHAT] ERROR: No messages to save")
            return jsonify({"error": "No messages to save"}), 400
        
        # SIMPLIFIED: Save any conversation content that exists
        # If there are messages, save them regardless of whether they're complete
        user_messages = [msg for msg in messages if msg.get('role') == 'user']
        assistant_messages = [msg for msg in messages if msg.get('role') == 'assistant']
        
        print(f"[SAVE_CHAT] User messages: {len(user_messages)}")
        print(f"[SAVE_CHAT] Assistant messages: {len(assistant_messages)}")
        
        # Log all message roles for debugging
        print(f"[SAVE_CHAT] All message roles: {[msg.get('role', 'unknown') for msg in messages]}")
        
        # Only require that there's at least one message with content
        if not messages:
            print("[SAVE_CHAT] ERROR: No messages to save")
            return jsonify({"error": "No messages to save"}), 400
        
        # Check that at least one message has meaningful content
        valid_messages = [msg for msg in messages if msg.get('content', '').strip()]
        if not valid_messages:
            print("[SAVE_CHAT] ERROR: No messages with content found")
            return jsonify({"error": "No messages with content to save"}), 400
        
        # Use first valid message for title generation (prefer user message if available)
        valid_user_messages = [msg for msg in user_messages if msg.get('content', '').strip() and len(msg.get('content', '').strip()) >= 3]
        
        # Debug: Print messages being saved to verify they're correct
        print(f"[SAVE_CHAT] Saving chat with {len(messages)} messages")
        print(f"[SAVE_CHAT] User messages: {len(user_messages)}, Assistant messages: {len(assistant_messages)}")
        
        # Get first message for title (prefer user message, fallback to assistant)
        first_msg_for_title = None
        if valid_user_messages:
            first_msg_for_title = valid_user_messages[0]
        elif assistant_messages:
            first_msg_for_title = assistant_messages[0]
        elif valid_messages:
            first_msg_for_title = valid_messages[0]
        
        if first_msg_for_title:
            print(f"[SAVE_CHAT] First message for title: role={first_msg_for_title.get('role')}, preview={first_msg_for_title.get('content', '')[:100]}")
        
        # STEP 4: Generate chat metadata and save/update file
        print("[SAVE_CHAT] STEP 4: Generating chat metadata and saving/updating file")
        
        # Get chat_history_html from request if provided
        chat_history_html = ''
        if request.json:
            chat_history_html = request.json.get('chat_history_html', '')
        
        if update_existing and existing_chat_id:
            # UPDATE existing chat
            chat_id = existing_chat_id
            print(f"[SAVE_CHAT] UPDATING existing chat with ID: {chat_id}")
            
            # Read existing chat to preserve original created_at
            existing_filename = get_chat_filename(chat_id)
            try:
                with open(existing_filename, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                created_at = existing_data.get('created_at', datetime.now().isoformat())
                print(f"[SAVE_CHAT] Preserving original created_at: {created_at}")
            except Exception as e:
                print(f"[SAVE_CHAT] Could not read existing file, using new timestamp: {e}")
                created_at = datetime.now().isoformat()
            
            # Generate new title from current messages
            title = get_chat_title(messages)
            print(f"[SAVE_CHAT] Updated title: {title}")
        else:
            # CREATE new chat
            chat_id = generate_chat_id()
            title = get_chat_title(messages)
            created_at = datetime.now().isoformat()
            print(f"[SAVE_CHAT] CREATING new chat with ID: {chat_id}")
            print(f"[SAVE_CHAT] Generated title: {title}")
        
        chat_data = {
            'id': chat_id,
            'title': title,
            'created_at': created_at,
            'updated_at': datetime.now().isoformat(),  # Track when it was last updated
            'messages': messages,  # EXACT messages from session - sent and received
            'chat_history_html': chat_history_html  # Full HTML from DOM - exact display state
        }
        
        filename = get_chat_filename(chat_id)
        print(f"[SAVE_CHAT] {'Updating' if update_existing else 'Saving'} to file: {filename}")
        print(f"[SAVE_CHAT] File will contain {len(messages)} messages exactly as sent/received")
        print(f"[SAVE_CHAT] File will contain chat_history_html ({len(chat_history_html)} chars) - full DOM state")
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(chat_data, f, indent=2, ensure_ascii=False)
        
        print(f"[SAVE_CHAT] Chat {'updated' if update_existing else 'saved'} successfully to {filename}")
        print("[SAVE_CHAT] Messages saved (AFTER save):")
        for idx, msg in enumerate(messages, 1):
            print(f"[SAVE_CHAT]   {idx}. role={msg.get('role')}, content_length={len(msg.get('content', ''))}")
        print("=" * 60)
        
        # Mark this chat as saved and update session
        session['saved_chat_id'] = chat_id
        session['current_chat_id'] = chat_id  # Also set as current chat
        
        # Always return JSON for regular requests
        # sendBeacon requests will get this JSON but won't be able to read it (which is fine)
        return jsonify({"status": "Chat saved", "chat_id": chat_id, "title": title})
    except Exception as e:
        print(f"ERROR in save_chat: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/list_chats', methods=['GET'])
def list_chats():
    """List all saved chat sessions"""
    try:
        chats = []
        for filename in sorted(CHATS_DIR.glob('*.json'), reverse=True):
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    chat_data = json.load(f)
                    chats.append({
                        'id': chat_data.get('id', filename.stem),
                        'title': chat_data.get('title', 'Untitled Chat'),
                        'created_at': chat_data.get('created_at', '')
                    })
            except Exception as e:
                print(f"Error reading chat file {filename}: {e}")
                continue
        
        return jsonify({"chats": chats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/load_chat/<chat_id>', methods=['GET'])
def load_chat(chat_id):
    """Load a specific chat session
    
    FLOW DOCUMENTATION:
    1. Read chat file from storage
    2. Extract all messages exactly as saved
    3. Load messages into session
    4. Return all messages to frontend for display
    
    This ensures the loaded chat contains EXACTLY what was saved.
    """
    print("=" * 60)
    print(f"[LOAD_CHAT] === LOAD_CHAT ENDPOINT CALLED ===")
    print(f"[LOAD_CHAT] Requested chat_id: {chat_id}")
    
    try:
        # STEP 1: Read chat file from storage
        print("[LOAD_CHAT] STEP 1: Reading chat file from storage")
        filename = get_chat_filename(chat_id)
        if not filename.exists():
            print(f"[LOAD_CHAT] ERROR: Chat file not found: {filename}")
            return jsonify({"error": "Chat not found"}), 404
        
        print(f"[LOAD_CHAT] Found chat file: {filename}")
        
        with open(filename, 'r', encoding='utf-8') as f:
            chat_data = json.load(f)
        
        # STEP 2: Extract all messages exactly as saved
        print("[LOAD_CHAT] STEP 2: Extracting messages from saved file")
        messages = chat_data.get('messages', [])
        
        print(f"[LOAD_CHAT] Loaded {len(messages)} messages from file")
        print("[LOAD_CHAT] Messages in file (BEFORE loading to session):")
        for idx, msg in enumerate(messages, 1):
            print(f"[LOAD_CHAT]   {idx}. role={msg.get('role')}, content_length={len(msg.get('content', ''))}")
            if msg.get('content'):
                preview = msg.get('content', '')[:100].replace('\n', ' ')
                print(f"[LOAD_CHAT]      Preview: {preview}...")
        
        # STEP 3: Load messages into session
        print("[LOAD_CHAT] STEP 3: Loading messages into session")
        session['messages'] = messages
        session['current_chat_id'] = chat_id  # Mark that we're in a loaded chat
        session['saved_chat_id'] = None  # Reset saved flag so this chat can be re-saved if modified
        
        print(f"[LOAD_CHAT] Session updated with {len(messages)} messages")
        print("[LOAD_CHAT] Messages in session (AFTER loading):")
        for idx, msg in enumerate(session['messages'], 1):
            print(f"[LOAD_CHAT]   {idx}. role={msg.get('role')}, content_length={len(msg.get('content', ''))}")
        
        # STEP 4: Get chat_history_html if available
        chat_history_html = chat_data.get('chat_history_html', '')
        print(f"[LOAD_CHAT] Chat history HTML length: {len(chat_history_html)}")
        if chat_history_html:
            print("[LOAD_CHAT] Chat history HTML available - will restore exact DOM state")
        else:
            print("[LOAD_CHAT] No chat history HTML - will render from messages")
        
        # STEP 5: Return all data to frontend
        print("[LOAD_CHAT] STEP 5: Returning all data to frontend")
        print(f"[LOAD_CHAT] Returning {len(messages)} messages exactly as saved")
        print("=" * 60)
        
        return jsonify({
            "status": "Chat loaded",
            "messages": messages,  # Return ALL messages exactly as saved
            "chat_history_html": chat_history_html,  # Return full HTML from DOM
            "title": chat_data.get('title', 'Untitled Chat')
        })
    except Exception as e:
        print(f"[LOAD_CHAT] ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/delete_chat/<chat_id>', methods=['DELETE'])
def delete_chat(chat_id):
    """Delete a chat session"""
    try:
        filename = get_chat_filename(chat_id)
        if not filename.exists():
            return jsonify({"error": "Chat not found"}), 404
        
        filename.unlink()
        return jsonify({"status": "Chat deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)