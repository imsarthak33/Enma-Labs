'use client';

import { useState } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import { Check } from 'lucide-react';
import Navbar from '@/components/Navbar';
import Footer from '@/components/Footer';
import { supabase } from '@/lib/supabase';
import { GlassButton } from '@/components/GlassButton';

export default function BookDemoPage() {
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [formData, setFormData] = useState({
    fullName: '',
    firmName: '',
    email: '',
    phone: '',
    date: '',
    time: '',
    message: '',
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    
    try {
      const payload: any = {
        name: formData.fullName,
        firm_name: formData.firmName,
        email: formData.email,
        phone: formData.phone,
        date: formData.date,
        time: formData.time,
        created_at: new Date().toISOString(),
      };
      
      if (formData.message.trim()) {
        payload.message = formData.message.trim();
      }

      const { error } = await supabase
        .from('demo_requests')
        .insert(payload);

      if (error) throw error;

      setSuccess(true);
      setFormData({
        fullName: '',
        firmName: '',
        email: '',
        phone: '',
        date: '',
        time: '',
        message: '',
      });
    } catch (error) {
      console.error('Error submitting demo request:', error);
      alert('Failed to submit demo request. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-black text-white font-sans flex flex-col">
      <Navbar />

      <main className="flex-1 pt-40 pb-24 flex items-center relative overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_50%,rgba(255,78,0,0.03),transparent)] pointer-events-none" />
        
        <div className="max-w-6xl mx-auto px-6 w-full relative z-10">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-24 items-start">
            {/* Left Info Panel */}
            <motion.div
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.6 }}
              className="pt-8"
            >
              <h1 className="font-serif text-5xl md:text-6xl font-medium leading-[1.1] mb-8">
                See Enma in Action
              </h1>
              <p className="text-[20px] text-white/50 leading-[1.6] mb-12 font-sans">
                Book a free 30-minute walkthrough. We&apos;ll show you Enma handling real CA workflows — live.
              </p>

              <div className="mb-12">
                <h3 className="font-serif text-2xl font-medium mb-8">What you&apos;ll see in the demo:</h3>
                <ol className="space-y-8">
                  {[
                    'Full secure Telegram onboarding flow with live token handshake',
                    'Enma processing a real invoice via OCR — live',
                    'DPA consent logging and audit trail walkthrough',
                    'Dashboard overview and client management',
                  ].map((item, i) => (
                    <li key={i} className="flex gap-6">
                      <div className="w-10 h-10 rounded-full bg-white/5 border border-white/10 flex items-center justify-center shrink-0 text-white/30 font-serif text-lg">
                        {i + 1}
                      </div>
                      <span className="text-white/70 mt-1.5 font-sans">{item}</span>
                    </li>
                  ))}
                </ol>
              </div>

              <div className="p-8 rounded-3xl bg-white/[0.02] border border-white/10 backdrop-blur-3xl">
                <p className="text-white/40 text-sm mb-2">Prefer to email us directly?</p>
                <a href="mailto:reach@enmalabs.com" className="text-accent hover:text-accent-hover font-bold text-xl transition-colors">
                  reach@enmalabs.com
                </a>
              </div>
            </motion.div>

            {/* Right Form Panel */}
            <motion.div
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.6, delay: 0.1 }}
              className="bg-white/[0.02] border border-white/10 rounded-[40px] p-10 md:p-14 shadow-2xl relative overflow-hidden backdrop-blur-3xl"
            >
              <div className="absolute top-0 right-0 w-96 h-96 bg-accent/5 blur-[100px] rounded-full pointer-events-none" />
              
              <div className="relative z-10">
                <h2 className="font-serif text-4xl font-medium mb-10">Book Your Free Demo</h2>

                {success ? (
                  <div className="py-16 text-center">
                    <div className="w-20 h-20 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mx-auto mb-8">
                      <Check className="w-10 h-10 text-emerald-400" />
                    </div>
                    <h3 className="font-serif text-3xl font-medium mb-4">Demo Requested!</h3>
                    <p className="text-white/50 mb-10 font-sans">We&apos;ve received your request and will be in touch shortly to confirm your slot.</p>
                    <GlassButton
                      onClick={() => setSuccess(false)}
                      variant="secondary"
                      className="px-8 py-4 mx-auto"
                    >
                      Book Another
                    </GlassButton>
                  </div>
                ) : (
                  <form onSubmit={handleSubmit} className="space-y-5">
                    <div>
                      <input
                        type="text"
                        name="fullName"
                        placeholder="Full Name (required)"
                        required
                        value={formData.fullName}
                        onChange={handleChange}
                        className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white px-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
                      />
                    </div>
                    <div>
                      <input
                        type="text"
                        name="firmName"
                        placeholder="Firm Name (required)"
                        required
                        value={formData.firmName}
                        onChange={handleChange}
                        className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white px-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
                      />
                    </div>
                    <div>
                      <input
                        type="email"
                        name="email"
                        placeholder="Email (required)"
                        required
                        value={formData.email}
                        onChange={handleChange}
                        className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white px-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
                      />
                    </div>
                    <div>
                      <input
                        type="tel"
                        name="phone"
                        placeholder="Phone Number (required)"
                        required
                        value={formData.phone}
                        onChange={handleChange}
                        className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white px-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <input
                          type="date"
                          name="date"
                          required
                          value={formData.date}
                          onChange={handleChange}
                          className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white px-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20 [color-scheme:dark]"
                        />
                      </div>
                      <div>
                        <input
                          type="time"
                          name="time"
                          required
                          value={formData.time}
                          onChange={handleChange}
                          className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white px-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20 [color-scheme:dark]"
                        />
                      </div>
                    </div>
                    <div>
                      <textarea
                        name="message"
                        placeholder="Message / Specific questions (optional)"
                        rows={4}
                        value={formData.message}
                        onChange={handleChange}
                        className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white px-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20 resize-none custom-scrollbar"
                      />
                    </div>

                    <GlassButton
                      type="submit"
                      variant="white"
                      className="w-full mt-4"
                      disabled={loading}
                    >
                      {loading ? 'Submitting...' : 'Book My Free Demo'}
                    </GlassButton>

                    <div className="flex items-center justify-center gap-2 mt-8 text-white/40 text-sm">
                      <Check className="w-4 h-4 text-emerald-400" />
                      We&apos;ll confirm your slot within 24 hours via email.
                    </div>
                  </form>
                )}
              </div>
            </motion.div>
          </div>
        </div>
      </main>

      <Footer />
    </div>
  );
}
