# JRI Assistant

A sophisticated chat assistant application that integrates the JRI protocol with a quality matrix system for enhanced conversational AI interactions.

## Features

- **Light/Dark Mode**: Beautiful purple gradient themes with smooth transitions
- **Chat History**: Save, load, and manage multiple chat sessions
- **Streaming Responses**: Real-time streaming of AI responses
- **Multiple Model Support**: Support for OpenAI and local models
- **Quality Matrix Integration**: 144 qualities across 12 conversational modes
- **Markdown Support**: Full markdown rendering with syntax highlighting

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create a `.env` file with your configuration:
```
OPENAI_API_KEY=your_api_key_here
LOCAL_BASE_URL=http://localhost:1234/v1
SECRET_KEY=your_secret_key_here
```

3. Ensure you have the required files:
   - `jri.tex` - JRI protocol file
   - `quality_matrix.json` - Quality matrix with 144 qualities across 12 modes

4. Run the application:
```bash
python app.py
```

5. Open your browser to `http://localhost:5000`

## Project Structure

```
JRI_Assistant/
├── app.py                 # Main Flask application
├── jri.tex               # JRI protocol
├── quality_matrix.json   # Quality matrix configuration
├── requirements.txt      # Python dependencies
├── static/               # Static assets (CSS, JS)
├── templates/            # HTML templates
└── chats/                # Saved chat sessions
```

## System Prompt

The system prompt combines:
1. JRI protocol from `jri.tex`
2. Quality matrix from `quality_matrix.json`
3. Critical instruction for CFP recursion and quality-mode adaptation

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

