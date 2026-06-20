'use client';

import { motion } from "framer-motion";
import { Send, Bot, Check, Paperclip } from "lucide-react";

export function TelegramPreview() {
  const messages = [
    {
      role: "user",
      content: "Hey Enma, I'm forwarding an invoice from ABC Corp. Can you process it?",
      time: "10:24 AM"
    },
    {
      role: "bot",
      content: "Processing invoice... Found: ABC Corp, Amount: ₹1,250.40, Tax: ₹125.04. I've reconciled this with your bank statement ending in *4432. Should I file this under Q3 Expenses?",
      time: "10:24 AM",
      status: "verified"
    },
    {
      role: "user",
      content: "Yes, please. Also, what's my current GST liability for this month?",
      time: "10:25 AM"
    },
    {
      role: "bot",
      content: "Your estimated GST liability for April is ₹4,820. This includes the 12 new invoices processed this week. Would you like a detailed breakdown?",
      time: "10:25 AM"
    }
  ];

  return (
    <motion.div 
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.8 }}
      className="w-full max-w-2xl mx-auto relative group px-6"
    >
      {/* Decorative Glow */}
      <div className="absolute -inset-1 bg-gradient-to-r from-accent/20 to-orange-500/20 rounded-[2rem] blur-2xl opacity-50 group-hover:opacity-75 transition duration-1000"></div>
      
      <div className="relative bg-white/[0.03] backdrop-blur-3xl rounded-[2rem] overflow-hidden border border-white/10 shadow-2xl">
        {/* Header */}
        <div className="bg-white/5 px-6 py-4 border-b border-white/10 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-accent/20 flex items-center justify-center border border-accent/30">
              <Bot className="w-6 h-6 text-accent" />
            </div>
            <div>
              <div className="text-white font-medium text-sm">Enma AI</div>
              <div className="text-accent text-[10px] font-medium uppercase tracking-widest">Chief of Staff</div>
            </div>
          </div>
          <div className="flex gap-2">
            <div className="w-2 h-2 rounded-full bg-white/10"></div>
            <div className="w-2 h-2 rounded-full bg-white/10"></div>
            <div className="w-2 h-2 rounded-full bg-white/10"></div>
          </div>
        </div>

        {/* Chat Area */}
        <div className="p-6 space-y-6 max-h-[400px] overflow-y-auto scrollbar-hide">
          {messages.map((msg, i) => (
            <motion.div 
              key={i} 
              initial={{ opacity: 0, x: msg.role === 'user' ? 10 : -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.1 }}
              className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}
            >
              <div className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                msg.role === 'user' 
                  ? 'bg-accent text-white rounded-tr-none' 
                  : 'bg-white/5 text-white/80 border border-white/10 rounded-tl-none'
              }`}>
                {msg.content}
                {msg.status === 'verified' && (
                  <div className="mt-2 flex items-center gap-1.5 text-[10px] text-accent font-medium bg-accent/10 w-fit px-2 py-0.5 rounded-full border border-accent/20">
                    <Check className="w-3 h-3" /> Verified & Reconciled
                  </div>
                )}
              </div>
              <div className="mt-1 text-[10px] text-white/20 px-1">{msg.time}</div>
            </motion.div>
          ))}
        </div>

        {/* Input Area */}
        <div className="p-4 bg-white/5 border-t border-white/10 flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-white/5 flex items-center justify-center text-white/40">
            <Paperclip className="w-5 h-5" />
          </div>
          <div className="flex-grow bg-white/5 rounded-full px-5 py-2.5 text-white/40 text-sm border border-white/5">
            Ask Enma anything...
          </div>
          <div className="w-10 h-10 rounded-full bg-accent/20 border border-accent/30 flex items-center justify-center text-accent shadow-[0_0_15px_rgba(255,78,0,0.2)]">
            <Send className="w-5 h-5" />
          </div>
        </div>
      </div>

      {/* Floating Badges */}
      <motion.div 
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="absolute -top-6 -right-6 hidden md:block"
      >
        <div className="bg-white/5 backdrop-blur-xl px-4 py-2 rounded-xl border border-white/10 flex items-center gap-2 shadow-xl animate-bounce">
          <div className="w-2 h-2 rounded-full bg-green-500"></div>
          <span className="text-[10px] font-medium text-white/80">Bank Feed Connected</span>
        </div>
      </motion.div>
      <motion.div 
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.4 }}
        className="absolute -bottom-6 -left-6 hidden md:block"
      >
        <div className="bg-white/5 backdrop-blur-xl px-4 py-2 rounded-xl border border-white/10 flex items-center gap-2 shadow-xl">
          <ShieldCheck className="w-4 h-4 text-accent" />
          <span className="text-[10px] font-medium text-white/80">GDPR Compliant</span>
        </div>
      </motion.div>
    </motion.div>
  );
}

function ShieldCheck({ className }: { className?: string }) {
  return (
    <svg 
      xmlns="http://www.w3.org/2000/svg" 
      width="24" 
      height="24" 
      viewBox="0 0 24 24" 
      fill="none" 
      stroke="currentColor" 
      strokeWidth="2" 
      strokeLinecap="round" 
      strokeLinejoin="round" 
      className={className}
    >
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}
