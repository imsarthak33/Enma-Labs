'use client';

import { motion } from 'framer-motion';
import type { Variants } from 'framer-motion';
import { FileText, Link as LinkIcon, ShieldCheck, ScrollText, Lock, Zap, Check, X } from 'lucide-react';
import Link from 'next/link';
import Image from 'next/image';
import Navbar from '@/components/Navbar';
import Footer from '@/components/Footer';
import ChatWidget from '@/components/ChatWidget';
import { TelegramPreview } from '@/components/TelegramPreview';
import { GlassButton } from '@/components/GlassButton';
import { TextScramble, ScrollProgressBar, TextReveal } from '@/components/FramerAnimations';
import { ContainerScroll } from '@/components/ui/container-scroll-animation';
import SmoothScroll from '@/components/SmoothScroll';
import { useRef } from 'react';

const fadeUpVariant: Variants = {
  hidden: { opacity: 0, y: 24 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.6, ease: 'easeOut' } },
};

const staggerContainer: Variants = {
  visible: { transition: { staggerChildren: 0.1 } },
};

export default function LandingPage() {
  const howItWorksRef = useRef<HTMLElement>(null);

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white selection:bg-blue-500/30 font-sans">
      <Navbar />

      <SmoothScroll>
        {/* SECTION 2 — HERO */}
        <section className="relative min-h-screen flex items-center justify-center pt-32 pb-20 overflow-hidden bg-black">
          {/* Background Image */}
          <div className="absolute inset-0 z-0">
            <Image
              src="/red-glow-bg.png"
              alt="Enma Hero Background"
              fill
              className="object-cover opacity-80"
              priority
              referrerPolicy="no-referrer"
            />
            <div className="absolute inset-0 bg-gradient-to-b from-black/60 via-transparent to-black" />
          </div>

          <div className="relative z-10 max-w-5xl mx-auto px-6 flex flex-col items-center text-center">
            <motion.div
              variants={staggerContainer}
              initial="hidden"
              animate="visible"
              className="flex flex-col items-center"
            >
              <motion.div variants={fadeUpVariant} className="mb-10">
                <span className="inline-flex items-center gap-2 px-4 py-1 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-[11px] font-medium tracking-wider uppercase">
                  <span className="bg-white text-black px-1.5 py-0.5 rounded-sm font-bold text-[9px]">New</span>
                  First Commercial Agent For CA Firms
                </span>
              </motion.div>

              <motion.h1
                variants={fadeUpVariant}
                className="font-serif text-6xl md:text-7xl lg:text-[90px] font-medium leading-[1] tracking-tight mb-10 text-white min-h-[2em]"
              >
                <TextReveal text="Meet Enma — The AI Chief of" delay={0} />
                <br />
                <TextReveal text="Staff Built for CA Firms" delay={0.8} />
              </motion.h1>

              <motion.p
                variants={fadeUpVariant}
                className="text-lg md:text-[18px] text-white/70 max-w-[700px] leading-[1.6] mb-12 font-sans"
              >
                Connect your firm to an AI that reads invoices, reconciles bank statements, handles tax queries, and onboards clients — all through Telegram. Enterprise-secure. Legally compliant.
              </motion.p>

              <motion.div variants={fadeUpVariant} className="flex flex-col sm:flex-row items-center gap-8 mb-24">
                <GlassButton
                  href="/signin"
                  variant="primary"
                  className="px-8 py-4 text-[14px]"
                >
                  Book Your Journey
                  <span className="group-hover:translate-x-1 transition-transform">→</span>
                </GlassButton>
                <GlassButton
                  className="flex items-center gap-3 text-white/80 hover:text-white transition-colors text-[14px] font-medium bg-transparent border-none shadow-none backdrop-blur-none"
                >
                  <div className="w-10 h-10 rounded-full border border-white/20 flex items-center justify-center">
                    <div className="w-0 h-0 border-t-[5px] border-t-transparent border-l-[8px] border-l-white border-b-[5px] border-b-transparent ml-1" />
                  </div>
                  Watch Launch
                </GlassButton>
              </motion.div>

              <motion.div variants={fadeUpVariant} className="flex flex-col items-center gap-8">
                <span className="text-[11px] font-medium tracking-[0.2em] uppercase text-white/40">
                  Partnering with leading space agencies worldwide
                </span>
                <div className="flex flex-wrap justify-center gap-12 opacity-50 grayscale hover:grayscale-0 transition-all">
                  {['Nova', 'Forge', 'Flux', 'Core', 'Echo'].map((logo) => (
                    <span key={logo} className="text-xl font-serif font-bold tracking-widest text-white italic">{logo}</span>
                  ))}
                </div>
              </motion.div>
            </motion.div>
          </div>
        </section>

        {/* SECTION 2.5 — TELEGRAM PREVIEW DEMO */}
        <section className="relative bg-black overflow-hidden pointer-events-none sm:pointer-events-auto">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_50%,rgba(255,78,0,0.05),transparent)] pointer-events-none" />
          <ContainerScroll
            titleComponent={
              <h2 className="text-4xl md:text-5xl lg:text-[6rem] font-serif font-medium mt-1 leading-[0.95] text-white eventis-gradient-text pb-4 mb-8 text-center relative z-10 tracking-tight">
                A Seamless Experience<br />Powered by Telegram
              </h2>
            }
          >
            <div className="w-full h-full flex flex-col justify-center transform scale-[0.85] sm:scale-100 origin-center">
              <TelegramPreview />
            </div>
          </ContainerScroll>
        </section>

        {/* SECTION 3 — SOCIAL PROOF STRIP */}
        <section className="py-20 border-y border-white/5 overflow-hidden relative bg-black">
          <div className="max-w-6xl mx-auto px-6 mb-10 text-center">
            <span className="text-[10px] font-bold tracking-[0.3em] uppercase text-white/30">
              Trusted Infrastructure Powering Enma Labs
            </span>
          </div>
          <div className="flex whitespace-nowrap overflow-hidden relative w-full">
            {/* Marquee Track */}
            <div className="flex animate-[marquee_40s_linear_infinite] min-w-max">
              {[...Array(2)].map((_, i) => (
                <div key={i} className="flex items-center gap-16 px-8">
                  {['Nova', 'Forge', 'Flux', 'Core', 'Echo', 'Vortex', 'Apex', 'Zenith'].map((badge, j) => (
                    <div key={j} className="text-xl font-serif font-bold tracking-[0.2em] text-white/20 italic">
                      {badge}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* SECTION 4 — PROBLEM STATEMENT */}
        <section className="py-40 relative bg-black overflow-hidden">
          <div className="glowing-orb animate-float top-0 -left-[20%] opacity-30" />
          <div className="absolute inset-0 bg-noise pointer-events-none" />
          <div className="max-w-6xl mx-auto px-6 relative z-10">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-24 items-center">
              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={staggerContainer}
                className="max-w-[680px]"
              >
                <motion.div variants={fadeUpVariant} className="mb-6">
                  <span className="text-[11px] font-bold tracking-[0.2em] uppercase text-accent">
                    The Problem
                  </span>
                </motion.div>
                <motion.h2 variants={fadeUpVariant} className="font-serif text-6xl md:text-7xl lg:text-[80px] font-medium leading-[0.95] mb-8 eventis-gradient-text pb-2">
                  Traditional CA Workflows Are Broken
                </motion.h2>
                <motion.p variants={fadeUpVariant} className="text-[20px] text-white/60 leading-[1.6] mb-12 font-sans">
                  Your team spends more time chasing documents than doing actual CA work. That&apos;s not a people problem — it&apos;s a systems problem.
                </motion.p>

                <div className="flex flex-col gap-10">
                  {[
                    { title: 'Hours lost daily', desc: 'manually collecting documents via WhatsApp and email with no audit trail' },
                    { title: 'Zero consent logging', desc: 'no legal record of when clients agreed to share their data' },
                    { title: 'Slow onboarding', desc: 'inconsistent, insecure client setup that varies per CA' },
                    { title: 'Manual tax queries', desc: 'every GST question requires a team member to respond personally' },
                  ].map((point, i) => (
                    <motion.div key={i} variants={fadeUpVariant} className="flex items-start gap-6">
                      <div className="mt-1 w-6 h-6 rounded-full bg-accent/10 flex items-center justify-center shrink-0 border border-accent/20">
                        <X className="w-3 h-3 text-accent" />
                      </div>
                      <div>
                        <span className="font-bold text-white tracking-tight">{point.title}</span>
                        <p className="text-white/50 text-sm mt-1">{point.desc}</p>
                      </div>
                    </motion.div>
                  ))}
                </div>
              </motion.div>

              {/* Abstract Visual */}
              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUpVariant}
                className="hidden lg:block relative h-[600px] rounded-3xl bg-white/[0.02] border border-white/10 backdrop-blur-3xl p-12 overflow-hidden"
              >
                <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_50%,rgba(255,255,255,0.03),transparent)]" />
                <div className="flex flex-col gap-6 opacity-20 blur-[2px]">
                  {[...Array(5)].map((_, i) => (
                    <div key={i} className="p-6 rounded-2xl bg-white/5 border border-white/10 flex items-center gap-6" style={{ transform: `translateX(${i * 20}px)` }}>
                      <div className="w-12 h-12 rounded-full bg-white/10" />
                      <div className="flex-1 space-y-3">
                        <div className="h-2 w-1/3 bg-white/20 rounded" />
                        <div className="h-2 w-2/3 bg-white/10 rounded" />
                      </div>
                    </div>
                  ))}
                </div>
              </motion.div>
            </div>
          </div>
        </section>

        {/* SECTION 5 — KEY FEATURES */}
        <section id="features" className="py-40 bg-black border-y border-white/5 relative overflow-hidden">
          <div className="glowing-orb-blue animate-float-delayed top-[20%] -right-[15%] opacity-40" />
          <div className="absolute inset-0 bg-noise pointer-events-none" />
          <div className="max-w-6xl mx-auto px-6 relative z-10">
            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true }}
              variants={staggerContainer}
              className="text-center mb-24"
            >
              <motion.div variants={fadeUpVariant} className="mb-6">
                <span className="text-[11px] font-bold tracking-[0.2em] uppercase text-accent">
                  Features
                </span>
              </motion.div>
              <motion.h2 variants={fadeUpVariant} className="font-serif text-6xl md:text-7xl lg:text-[80px] font-medium leading-[0.95] eventis-gradient-text pb-2">
                Everything Your Firm Needs.<br />
                Nothing It Doesn&apos;t.
              </motion.h2>
            </motion.div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              {/* Large Card */}
              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUpVariant}
                whileHover={{ y: -8, transition: { duration: 0.2 } }}
                className="md:col-span-2 bg-white/[0.02] border border-white/10 backdrop-blur-3xl rounded-3xl p-10 flex flex-col justify-between overflow-hidden relative group"
              >
                <div className="absolute top-0 right-0 w-96 h-96 bg-accent/5 blur-[100px] rounded-full pointer-events-none group-hover:bg-accent/10 transition-all" />
                <div className="relative z-10">
                  <div className="w-14 h-14 rounded-2xl bg-accent/10 flex items-center justify-center mb-8 border border-accent/20">
                    <FileText className="w-7 h-7 text-accent" />
                  </div>
                  <h3 className="font-serif text-3xl font-medium mb-6">Instant Invoice & Statement Processing</h3>
                  <p className="text-white/50 leading-[1.8] max-w-md text-lg">
                    Clients forward invoices and bank statements directly to Enma on Telegram. Enma performs OCR extraction and tax reconciliation automatically — no manual data entry needed.
                  </p>
                </div>
              </motion.div>

              {/* Standard Card */}
              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUpVariant}
                whileHover={{ y: -8, transition: { duration: 0.2 } }}
                className="bg-white/[0.02] border border-white/10 backdrop-blur-3xl rounded-3xl p-10 group"
              >
                <div className="w-14 h-14 rounded-2xl bg-accent/10 flex items-center justify-center mb-8 border border-accent/20">
                  <LinkIcon className="w-7 h-7 text-accent" />
                </div>
                <h3 className="font-serif text-2xl font-medium mb-6">One-Click Secure Client Onboarding</h3>
                <p className="text-white/50 leading-[1.8]">
                  Clients receive a unique, time-limited deep link. Once they click it, their identity is verified and Telegram is securely bound to your firm — no passwords, no confusion.
                </p>
              </motion.div>

              {/* Row 2 - Three Cards */}
              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUpVariant}
                whileHover={{ y: -8, transition: { duration: 0.2 } }}
                className="bg-white/[0.02] border border-white/10 backdrop-blur-3xl rounded-3xl p-10 group"
              >
                <div className="w-14 h-14 rounded-2xl bg-accent/10 flex items-center justify-center mb-8 border border-accent/20">
                  <ShieldCheck className="w-7 h-7 text-accent" />
                </div>
                <h3 className="font-serif text-2xl font-medium mb-6">Token-Based Auth. Zero IDOR Risk.</h3>
                <p className="text-white/50 leading-[1.8]">
                  Every onboarding link uses a cryptographically secure one-time token that expires in 30 minutes. Raw firm IDs are never exposed.
                </p>
              </motion.div>

              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUpVariant}
                whileHover={{ y: -8, transition: { duration: 0.2 } }}
                className="bg-white/[0.02] border border-white/10 backdrop-blur-3xl rounded-3xl p-10 group"
              >
                <div className="w-14 h-14 rounded-2xl bg-accent/10 flex items-center justify-center mb-8 border border-accent/20">
                  <ScrollText className="w-7 h-7 text-accent" />
                </div>
                <h3 className="font-serif text-2xl font-medium mb-6">Built-In Data Processing Agreement</h3>
                <p className="text-white/50 leading-[1.8]">
                  Before any firm connects, they digitally sign a legally binding DPA. Consent is timestamped and logged server-side — permanently auditable.
                </p>
              </motion.div>

              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUpVariant}
                whileHover={{ y: -8, transition: { duration: 0.2 } }}
                className="bg-white/[0.02] border border-white/10 backdrop-blur-3xl rounded-3xl p-10 group"
              >
                <div className="w-14 h-14 rounded-2xl bg-accent/10 flex items-center justify-center mb-8 border border-accent/20">
                  <Zap className="w-7 h-7 text-accent" />
                </div>
                <h3 className="font-serif text-2xl font-medium mb-6">Tax Queries Answered in Seconds</h3>
                <p className="text-white/50 leading-[1.8]">
                  Clients send questions directly to Enma. GST reconciliation, document requests, and standard tax queries handled instantly — freeing your team completely.
                </p>
              </motion.div>
            </div>
          </div>
        </section>

        {/* SECTION 6 — SECURITY DEEP DIVE */}
        <section id="security" className="py-40 relative bg-black overflow-hidden">
          <div className="glowing-orb animate-float bottom-0 -left-[10%] opacity-40" />
          <div className="max-w-6xl mx-auto px-6 relative z-10">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-24 items-center">
              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={staggerContainer}
              >
                <motion.div variants={fadeUpVariant} className="mb-6">
                  <span className="text-[11px] font-bold tracking-[0.2em] uppercase text-accent">
                    Security
                  </span>
                </motion.div>
                <motion.h2 variants={fadeUpVariant} className="font-serif text-6xl md:text-7xl lg:text-[80px] font-medium leading-[0.95] mb-8 eventis-gradient-text pb-2">
                  Enterprise Security You Can<br />
                  Actually Explain to Clients
                </motion.h2>
                <motion.p variants={fadeUpVariant} className="text-[20px] text-white/60 leading-[1.6] mb-12 font-sans">
                  We didn&apos;t bolt security on after the fact. The entire V2 architecture was rebuilt from the ground up to eliminate every known attack surface.
                </motion.p>

                <div className="flex flex-col gap-10">
                  {[
                    { title: 'Token-Based Telegram Handshake', desc: 'Raw firm IDs are never transmitted in any link or URL' },
                    { title: '30-Minute Token Expiry', desc: 'One-time tokens are cryptographically invalidated immediately after use' },
                    { title: 'Server-Side Consent Logging', desc: 'IP address, timestamp, and firm ID captured at the exact moment of DPA acceptance' },
                    { title: 'Logical Data Isolation', desc: 'Every CA firm\'s data lives in a fully separated logical partition' },
                  ].map((point, i) => (
                    <motion.div key={i} variants={fadeUpVariant} className="flex items-start gap-6">
                      <div className="mt-1 w-6 h-6 rounded-full bg-accent/10 flex items-center justify-center shrink-0 border border-accent/20">
                        <ShieldCheck className="w-3.5 h-3.5 text-accent" />
                      </div>
                      <div>
                        <span className="font-bold text-white tracking-tight">{point.title}</span>
                        <p className="text-white/50 text-sm mt-1">{point.desc}</p>
                      </div>
                    </motion.div>
                  ))}
                </div>
              </motion.div>

              {/* Security Visual */}
              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUpVariant}
                className="hidden lg:block bg-white/[0.02] border border-white/10 rounded-3xl p-10 shadow-2xl backdrop-blur-3xl"
              >
                <div className="flex items-center gap-3 mb-8 pb-8 border-b border-white/5">
                  <Lock className="w-5 h-5 text-white/30" />
                  <span className="text-[10px] font-bold tracking-[0.2em] uppercase text-white/30">SECURITY_AUDIT_LOG</span>
                </div>
                <div className="space-y-5 font-mono text-[11px]">
                  {[
                    { time: '14:23:01', action: 'TOKEN_GENERATED', status: 'VALID', color: 'text-accent' },
                    { time: '14:24:12', action: 'DPA_CONSENT_LOGGED', status: 'SECURE', color: 'text-emerald-400' },
                    { time: '14:24:15', action: 'TELEGRAM_HANDSHAKE', status: 'SUCCESS', color: 'text-emerald-400' },
                    { time: '14:24:16', action: 'TOKEN_STATUS', status: 'INVALIDATED', color: 'text-white/20' },
                    { time: '14:30:00', action: 'DATA_PARTITION', status: 'ISOLATED', color: 'text-emerald-400' },
                  ].map((log, i) => (
                    <div key={i} className="flex items-center justify-between p-4 rounded-xl bg-white/[0.02] border border-white/[0.02]">
                      <div className="flex items-center gap-6">
                        <span className="text-white/20">{log.time}</span>
                        <span className="text-white/60">{log.action}</span>
                      </div>
                      <span className={log.color}>[{log.status}]</span>
                    </div>
                  ))}
                </div>
              </motion.div>
            </div>
          </div>
        </section>

        {/* SECTION 7 — HOW IT WORKS */}
        <section id="how-it-works" ref={howItWorksRef} className="py-40 bg-black border-y border-white/5 relative overflow-hidden">
          <div className="absolute inset-0 bg-noise pointer-events-none" />
          <div className="max-w-4xl mx-auto px-6 relative z-10">
            {/* Progress Bar */}
            <div className="absolute -left-12 top-0 bottom-0 hidden lg:block">
              <ScrollProgressBar containerRef={howItWorksRef} />
            </div>

            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true }}
              variants={staggerContainer}
              className="text-center mb-24"
            >
              <motion.div variants={fadeUpVariant} className="mb-6">
                <span className="text-[11px] font-bold tracking-[0.2em] uppercase text-accent">
                  How It Works
                </span>
              </motion.div>
              <motion.h2 variants={fadeUpVariant} className="font-serif text-6xl md:text-7xl lg:text-[80px] font-medium leading-[0.95] eventis-gradient-text pb-2">
                Up and Running in 3 Steps
              </motion.h2>
            </motion.div>

            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true }}
              variants={{
                visible: { transition: { staggerChildren: 0.15 } }
              }}
              className="flex flex-col gap-8"
            >
              {[
                { num: '01', title: 'Register & Sign the DPA', desc: 'Create your CA firm account and digitally sign the legally binding Data Processing Agreement. Your consent is logged server-side instantly.' },
                { num: '02', title: 'Connect Enma to Telegram', desc: 'Click "Connect Enma to Telegram." A cryptographically secure, one-time deep link is generated. Click it — your Telegram is permanently and securely bound to your firm. The link self-destructs after use.' },
                { num: '03', title: 'Let Enma Handle the Rest', desc: 'Forward client invoices, bank statements, and tax queries to Enma on Telegram. Your AI Chief of Staff processes, reconciles, and responds — instantly and automatically.' },
              ].map((step, i) => (
                <motion.div
                  key={i}
                  variants={fadeUpVariant}
                  className="bg-white/[0.02] border border-white/10 backdrop-blur-3xl rounded-3xl p-10 md:p-14 flex flex-col md:flex-row gap-12 items-start group"
                >
                  <span className="font-serif text-8xl font-bold text-white/5 leading-none group-hover:text-accent/10 transition-colors">{step.num}</span>
                  <div>
                    <h3 className="font-serif text-3xl font-medium mb-6">{step.title}</h3>
                    <p className="text-white/50 leading-[1.8] text-lg font-sans">{step.desc}</p>
                  </div>
                </motion.div>
              ))}
            </motion.div>
          </div>
        </section>

        {/* SECTION 8 — PRICING */}
        <section id="pricing" className="py-40 relative bg-black">
          <div className="max-w-6xl mx-auto px-6">
            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true }}
              variants={staggerContainer}
              className="text-center mb-24"
            >
              <motion.div variants={fadeUpVariant} className="mb-6">
                <span className="text-[11px] font-bold tracking-[0.2em] uppercase text-accent">
                  Pricing
                </span>
              </motion.div>
              <motion.h2 variants={fadeUpVariant} className="font-serif text-6xl md:text-7xl lg:text-[80px] font-medium leading-[0.95] eventis-gradient-text pb-2">
                Simple Pricing for Growing CA Firms
              </motion.h2>
            </motion.div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-10 items-center max-w-5xl mx-auto">
              {/* Starter */}
              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUpVariant}
                className="bg-white/[0.02] border border-white/10 backdrop-blur-3xl rounded-3xl p-10"
              >
                <h3 className="font-serif text-2xl font-medium mb-2">Starter</h3>
                <p className="text-white/40 text-sm mb-10 pb-10 border-b border-white/5">For solo CAs</p>
                <ul className="space-y-5 mb-10 text-sm text-white/60">
                  <li className="flex items-center gap-4"><Check className="w-4 h-4 text-accent" /> Limited clients</li>
                  <li className="flex items-center gap-4"><Check className="w-4 h-4 text-accent" /> Core Telegram processing</li>
                  <li className="flex items-center gap-4"><Check className="w-4 h-4 text-accent" /> Basic DPA logging</li>
                </ul>
                <GlassButton href="/signin" variant="primary" className="block w-full text-center px-6 py-4 text-[13px]">
                  Get Started Free
                </GlassButton>
              </motion.div>

              {/* Professional */}
              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUpVariant}
                className="bg-white/[0.05] border border-accent/30 shadow-[0_0_60px_rgba(255,78,0,0.1)] rounded-3xl p-12 relative md:-mt-10 md:mb-10 z-10 backdrop-blur-3xl"
              >
                <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-accent text-white text-[9px] font-bold uppercase tracking-[0.2em] py-1.5 px-4 rounded-full">
                  Most Popular
                </div>
                <h3 className="font-serif text-3xl font-medium mb-2 text-white">Professional</h3>
                <p className="text-white/40 text-sm mb-10 pb-10 border-b border-white/5">For mid-size firms</p>
                <ul className="space-y-5 mb-10 text-sm text-white/80">
                  <li className="flex items-center gap-4"><Check className="w-4 h-4 text-accent" /> Unlimited clients</li>
                  <li className="flex items-center gap-4"><Check className="w-4 h-4 text-accent" /> Full DPA consent audit trail</li>
                  <li className="flex items-center gap-4"><Check className="w-4 h-4 text-accent" /> Priority support</li>
                  <li className="flex items-center gap-4"><Check className="w-4 h-4 text-accent" /> All features included</li>
                </ul>
                <GlassButton href="/signin" variant="white" className="block w-full text-center px-6 py-4 text-[13px]">
                  Start Free Trial
                </GlassButton>
              </motion.div>

              {/* Enterprise */}
              <motion.div
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUpVariant}
                className="bg-white/[0.02] border border-white/10 backdrop-blur-3xl rounded-3xl p-10"
              >
                <h3 className="font-serif text-2xl font-medium mb-2">Enterprise</h3>
                <p className="text-white/40 text-sm mb-10 pb-10 border-b border-white/5">For large CA firms</p>
                <ul className="space-y-5 mb-10 text-sm text-white/60">
                  <li className="flex items-center gap-4"><Check className="w-4 h-4 text-accent" /> Dedicated data isolation</li>
                  <li className="flex items-center gap-4"><Check className="w-4 h-4 text-accent" /> Custom SLA</li>
                  <li className="flex items-center gap-4"><Check className="w-4 h-4 text-accent" /> Custom integrations</li>
                  <li className="flex items-center gap-4"><Check className="w-4 h-4 text-accent" /> Dedicated account manager</li>
                </ul>
                <GlassButton href="/book-demo" variant="primary" className="block w-full text-center px-6 py-4 text-[13px]">
                  Contact Sales
                </GlassButton>
              </motion.div>
            </div>

            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true }}
              variants={fadeUpVariant}
              className="text-center mt-16"
            >
              <p className="text-white/40 font-sans">
                Not sure which plan fits? <Link href="/book-demo" className="text-accent hover:text-accent-hover underline underline-offset-8 transition-colors">Book a free demo first.</Link>
              </p>
            </motion.div>
          </div>
        </section>

        {/* SECTION 9 — FINAL CTA BANNER */}
        <section className="py-40 relative overflow-hidden bg-black">
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_60%_50%_at_50%_50%,rgba(255,78,0,0.05),transparent)] pointer-events-none" />
          <div className="max-w-4xl mx-auto px-6 relative z-10">
            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true }}
              variants={fadeUpVariant}
              className="bg-white/[0.02] border border-white/10 rounded-[40px] p-16 md:p-24 text-center relative overflow-hidden backdrop-blur-3xl"
            >
              <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full h-full bg-[radial-gradient(circle_at_top,rgba(255,78,0,0.1),transparent_70%)] pointer-events-none" />

              <h2 className="font-serif text-5xl md:text-6xl lg:text-[70px] font-medium leading-[0.95] mb-8 relative z-10 eventis-gradient-text pb-2">
                Ready to Give Your CA Firm<br />
                an AI Chief of Staff?
              </h2>
              <p className="text-lg text-white/50 max-w-2xl mx-auto leading-[1.8] mb-12 relative z-10 font-sans">
                Join CA firms already using Enma to automate operations, stay compliant, and serve more clients — without hiring more staff.
              </p>
              <div className="flex flex-col sm:flex-row justify-center gap-6 relative z-10">
                <GlassButton
                  href="/signin"
                  variant="white"
                  className="px-10 py-5 text-[14px]"
                >
                  Create Your Free Account
                </GlassButton>
                <GlassButton
                  href="/book-demo"
                  variant="secondary"
                  className="px-10 py-5 text-[14px]"
                >
                  Talk to Us First
                </GlassButton>
              </div>
            </motion.div>
          </div>
        </section>

        <Footer />
      </SmoothScroll>
      <ChatWidget />
    </div>
  );
}
