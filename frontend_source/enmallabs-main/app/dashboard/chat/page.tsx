'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { useDashboard } from '../dashboard-context';
import { useChatLogs, formatRelativeTime } from '@/hooks/use-dashboard-data';
import type { ChatLogRecord } from '@/hooks/use-dashboard-data';
import { getSupabase } from '@/lib/supabase';
import { EmptyState } from '@/components/dashboard/EmptyState';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Send, Bot, User, MessageCircle, Search, X,
  Loader2, Zap, ExternalLink, ChevronDown,
} from 'lucide-react';

export default function ChatPage() {
  const { firmData, firmLoading } = useDashboard();
  const { chatLogs, loading } = useChatLogs(firmData?.id || null, 200);
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [showSearch, setShowSearch] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    if (autoScroll && messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatLogs, autoScroll]);

  // Detect scroll position
  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    setAutoScroll(isNearBottom);
  }, []);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    setAutoScroll(true);
  };

  // Reverse to show oldest first (chat order)
  const sortedLogs = [...chatLogs].reverse();

  const filteredLogs = searchQuery
    ? sortedLogs.filter(
        (log) =>
          log.user_message.toLowerCase().includes(searchQuery.toLowerCase()) ||
          log.bot_response.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : sortedLogs;

  // Group messages by date
  const groupedMessages = filteredLogs.reduce<{ date: string; messages: ChatLogRecord[] }[]>((groups, msg) => {
    const date = new Date(msg.created_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
    const lastGroup = groups[groups.length - 1];
    if (lastGroup && lastGroup.date === date) {
      lastGroup.messages.push(msg);
    } else {
      groups.push({ date, messages: [msg] });
    }
    return groups;
  }, []);

  const handleSend = async () => {
    if (!message.trim() || !firmData?.id || sending) return;

    const userMsg = message.trim();
    setMessage('');
    setSending(true);

    try {
      // Send the message to our Next.js API route, which forwards it as a simulated Webhook to the FastAPI backend
      const res = await fetch('/api/chat/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMsg,
          telegramChatId: firmData.telegram_chat_id,
        }),
      });

      if (!res.ok) {
        throw new Error('Failed to sync message with backend');
      }
      
      // The FastAPI backend will process this webhook, send the reply to Telegram via its Bot Token,
      // and log both the user message and bot response to Supabase. 
      // Because we use Supabase Realtime in useChatLogs, the UI will automatically update!
      
    } catch (err) {
      console.error('Failed to send message:', err);
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (firmLoading) {
    return (
      <div className="max-w-5xl mx-auto h-full flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-accent animate-spin" />
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto flex flex-col" style={{ height: 'calc(100vh - 128px)' }}>
      {/* Chat Header */}
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div className="flex items-center gap-4">
          <div className="relative">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-[#229ED9]/20 to-blue-600/20 border border-[#229ED9]/20 flex items-center justify-center">
              <Bot className="w-6 h-6 text-[#229ED9]" />
            </div>
            <div className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 bg-emerald-500 rounded-full border-2 border-black" />
          </div>
          <div>
            <h1 className="font-serif text-2xl font-medium">Enma AI Chat</h1>
            <p className="text-xs text-white/30">
              {firmData?.telegram_chat_id ? (
                <span className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full inline-block" />
                  Synced with Telegram • {chatLogs.length} messages
                </span>
              ) : (
                'Chat history from your Telegram interactions'
              )}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowSearch(!showSearch)}
            className={`w-10 h-10 rounded-xl flex items-center justify-center transition-all ${showSearch ? 'bg-accent/20 text-accent' : 'bg-white/5 text-white/40 hover:text-white'}`}
          >
            <Search className="w-5 h-5" />
          </button>
          <a
            href="https://t.me/enma12bot"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-[#229ED9]/10 border border-[#229ED9]/20 text-[#229ED9] text-xs font-bold hover:bg-[#229ED9]/20 transition-all"
          >
            <Send className="w-3.5 h-3.5" />Open in Telegram
            <ExternalLink className="w-3 h-3" />
          </a>
        </div>
      </div>

      {/* Search Bar */}
      <AnimatePresence>
        {showSearch && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden shrink-0">
            <div className="flex items-center gap-2 bg-white/5 border border-white/10 rounded-2xl px-4 py-2.5 mb-4">
              <Search className="w-4 h-4 text-white/30" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search messages..."
                className="bg-transparent border-none outline-none text-sm w-full placeholder:text-white/20"
                autoFocus
              />
              {searchQuery && (
                <button onClick={() => setSearchQuery('')} className="text-white/30 hover:text-white"><X className="w-4 h-4" /></button>
              )}
              <span className="text-[10px] text-white/20 font-mono shrink-0">{filteredLogs.length} results</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Messages Area */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto custom-scrollbar glass-card rounded-[24px] p-0 relative"
      >
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <div className="flex flex-col items-center gap-3">
              <Loader2 className="w-6 h-6 text-accent animate-spin" />
              <p className="text-xs text-white/30">Loading conversation...</p>
            </div>
          </div>
        ) : filteredLogs.length === 0 ? (
          <div className="flex items-center justify-center h-full p-8">
            <EmptyState
              icon={MessageCircle}
              title={searchQuery ? 'No Messages Found' : 'No Conversations Yet'}
              description={
                searchQuery
                  ? 'Try adjusting your search query.'
                  : firmData?.telegram_chat_id
                  ? 'Start chatting with Enma on Telegram or send a message below. Your conversations will sync here in real-time.'
                  : 'Connect your Telegram account to start chatting with Enma AI. All conversations will appear here.'
              }
            />
          </div>
        ) : (
          <div className="p-6 space-y-1">
            {groupedMessages.map((group) => (
              <div key={group.date}>
                {/* Date Separator */}
                <div className="flex items-center justify-center my-6">
                  <div className="px-4 py-1.5 rounded-full bg-white/5 border border-white/10">
                    <span className="text-[10px] font-bold text-white/30 uppercase tracking-wider">{group.date}</span>
                  </div>
                </div>

                {/* Messages */}
                {group.messages.map((log) => (
                  <ChatMessage key={log.id} log={log} />
                ))}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* Scroll to bottom button */}
        <AnimatePresence>
          {!autoScroll && (
            <motion.button
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              onClick={scrollToBottom}
              className="absolute bottom-4 right-4 w-10 h-10 rounded-full bg-accent/20 border border-accent/30 flex items-center justify-center text-accent hover:bg-accent/30 transition-all shadow-lg"
            >
              <ChevronDown className="w-5 h-5" />
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      {/* Message Input */}
      <div className="mt-4 shrink-0">
        <div className="glass-card rounded-2xl p-3 flex items-end gap-3">
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={firmData?.telegram_chat_id ? 'Type a message to Enma AI...' : 'Connect Telegram to start chatting...'}
            disabled={!firmData?.telegram_chat_id}
            rows={1}
            className="flex-1 bg-transparent border-none outline-none text-sm placeholder:text-white/20 resize-none max-h-32 disabled:opacity-30 py-2"
            style={{ minHeight: '40px' }}
          />
          <button
            onClick={handleSend}
            disabled={!message.trim() || !firmData?.telegram_chat_id || sending}
            className="w-10 h-10 rounded-xl bg-accent/20 border border-accent/30 flex items-center justify-center text-accent hover:bg-accent/30 transition-all disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
          >
            {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </div>
        <p className="text-[10px] text-white/20 text-center mt-2">
          {firmData?.telegram_chat_id
            ? 'Messages are synced with your Telegram bot in real-time'
            : 'Connect Telegram in Settings to enable chat'}
        </p>
      </div>
    </div>
  );
}

// ─── Chat Message Bubble ─────────────────────────────────────────────────────

function ChatMessage({ log }: { log: ChatLogRecord }) {
  const time = new Date(log.created_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });

  return (
    <div className="space-y-2 mb-4">
      {/* User Message */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex justify-end"
      >
        <div className="max-w-[75%] flex items-end gap-2">
          <div className="bg-accent/15 border border-accent/20 rounded-2xl rounded-br-md px-4 py-3">
            <p className="text-sm text-white/90 whitespace-pre-wrap leading-relaxed">{log.user_message}</p>
            <div className="flex items-center justify-end gap-2 mt-1.5">
              {log.intent_tag && log.intent_tag !== 'unknown' && <StatusBadge status={log.intent_tag} />}
              <span className="text-[10px] text-white/20">{time}</span>
            </div>
          </div>
          <div className="w-7 h-7 rounded-full bg-accent/20 flex items-center justify-center shrink-0">
            <User className="w-3.5 h-3.5 text-accent" />
          </div>
        </div>
      </motion.div>

      {/* Bot Response */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className="flex justify-start"
      >
        <div className="max-w-[75%] flex items-end gap-2">
          <div className="w-7 h-7 rounded-full bg-[#229ED9]/20 flex items-center justify-center shrink-0">
            <Bot className="w-3.5 h-3.5 text-[#229ED9]" />
          </div>
          <div className="bg-white/[0.04] border border-white/10 rounded-2xl rounded-bl-md px-4 py-3">
            <p className="text-sm text-white/80 whitespace-pre-wrap leading-relaxed">{log.bot_response}</p>
            <div className="flex items-center gap-2 mt-1.5">
              <span className="text-[10px] text-white/20">{time}</span>
              {log.response_time_ms && (
                <span className="text-[10px] text-white/15 flex items-center gap-0.5">
                  <Zap className="w-2.5 h-2.5" />{log.response_time_ms}ms
                </span>
              )}
              {log.is_error && <span className="text-[10px] text-red-400 font-bold">Error</span>}
            </div>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
