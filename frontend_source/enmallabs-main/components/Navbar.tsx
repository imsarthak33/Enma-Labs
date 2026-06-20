'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { Menu, X, User } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { GlassButton } from './GlassButton';

export default function Navbar() {
  const [isScrolled, setIsScrolled] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 20);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <header className="fixed top-6 left-0 right-0 z-50 px-6">
      <div className="max-w-6xl mx-auto flex items-center justify-between bg-white/5 backdrop-blur-xl border border-white/10 rounded-full px-8 py-3">
      <Link href="/" className="flex items-center gap-2">
        <div className="text-4xl font-serif italic text-white tracking-wide drop-shadow-md">
          Enma Labs
        </div>
      </Link>

        {/* Desktop Nav */}
        <nav className="hidden md:flex items-center gap-6">
          {['Home', 'Features', 'Security', 'How It Works', 'Pricing'].map((item) => (
            <a
              key={item}
              href={item === 'Home' ? '/' : `#${item.toLowerCase().replace(/ /g, '-')}`}
              className="text-[13px] font-medium text-white/70 hover:text-white transition-colors"
            >
              {item}
            </a>
          ))}
        </nav>

        <div className="hidden md:flex items-center gap-3">
          <Link
            href="/login"
            className="text-[13px] font-medium text-white/70 hover:text-white transition-colors px-4"
          >
            Login
          </Link>
          <GlassButton
            href="/book-demo"
            variant="white"
            className="px-5 py-2"
          >
            Book a Demo
            <span className="text-[10px]">↗</span>
          </GlassButton>
        </div>

        {/* Mobile Menu Toggle */}
        <button
          className="md:hidden text-white/70 hover:text-white"
          onClick={() => setMobileMenuOpen(true)}
        >
          <Menu className="w-5 h-5" />
        </button>
      </div>

      {/* Mobile Drawer */}
      <AnimatePresence>
        {mobileMenuOpen && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 md:hidden"
              onClick={() => setMobileMenuOpen(false)}
            />
            <motion.div
              initial={{ x: '100%' }}
              animate={{ x: 0 }}
              exit={{ x: '100%' }}
              transition={{ type: 'spring', damping: 25, stiffness: 200 }}
              className="fixed top-0 right-0 bottom-0 w-72 bg-black border-l border-white/10 z-50 p-8 flex flex-col md:hidden"
            >
              <div className="flex justify-end mb-12">
                <button
                  className="text-white/30 hover:text-white"
                  onClick={() => setMobileMenuOpen(false)}
                >
                  <X className="w-6 h-6" />
                </button>
              </div>
              <nav className="flex flex-col gap-8 mb-12">
                {['Home', 'Features', 'Security', 'How It Works', 'Pricing'].map((item) => (
                  <a
                    key={item}
                    href={item === 'Home' ? '/' : `#${item.toLowerCase().replace(/ /g, '-')}`}
                    className="text-2xl font-serif text-white/50 hover:text-white transition-colors"
                    onClick={() => setMobileMenuOpen(false)}
                  >
                    {item}
                  </a>
                ))}
              </nav>
              <div className="flex flex-col gap-4 mt-auto">
                <GlassButton
                  href="/login"
                  variant="secondary"
                  className="w-full"
                  onClick={() => setMobileMenuOpen(false)}
                >
                  <User className="w-4 h-4" />
                  Login
                </GlassButton>
                <GlassButton
                  href="/book-demo"
                  variant="white"
                  className="w-full"
                  onClick={() => setMobileMenuOpen(false)}
                >
                  Book a Demo
                </GlassButton>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </header>
  );
}
