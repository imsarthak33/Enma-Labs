import { NextResponse } from 'next/server';

export async function POST(req: Request) {
  try {
    const { message, telegramChatId } = await req.json();

    if (!message || !telegramChatId) {
      return NextResponse.json({ error: 'Missing message or telegramChatId' }, { status: 400 });
    }

    const backendUrl = process.env.BACKEND_WEBHOOK_URL;
    
    if (!backendUrl) {
      console.warn('BACKEND_WEBHOOK_URL is not set. The message was recorded locally but not sent to the AI agent or Telegram.');
      return NextResponse.json({ 
        success: true, 
        simulated: true, 
        warning: 'Backend URL not configured.' 
      });
    }

    // Construct a simulated Telegram Webhook payload
    // This allows the FastAPI backend to process the web message exactly as if it came from Telegram
    const simulatedWebhookPayload = {
      update_id: Date.now(),
      message: {
        message_id: Date.now(),
        from: {
          id: parseInt(telegramChatId, 10),
          is_bot: false,
          first_name: "Web Dashboard User"
        },
        chat: {
          id: parseInt(telegramChatId, 10),
          type: "private"
        },
        date: Math.floor(Date.now() / 1000),
        text: message
      }
    };

    // Forward the payload to the FastAPI backend
    const response = await fetch(backendUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(simulatedWebhookPayload),
    });

    if (!response.ok) {
      throw new Error(`Backend responded with status: ${response.status}`);
    }

    return NextResponse.json({ success: true });
    
  } catch (error: any) {
    console.error('API /api/chat/send error:', error);
    return NextResponse.json({ error: error.message || 'Internal Server Error' }, { status: 500 });
  }
}
