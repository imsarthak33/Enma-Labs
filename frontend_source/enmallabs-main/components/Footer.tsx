import Link from 'next/link';

export default function Footer() {
  return (
    <footer className="py-24 border-t border-white/5 bg-black">
      <div className="max-w-6xl mx-auto px-6">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-12 mb-16">
          {/* Column 1 */}
          <div className="flex flex-col gap-6">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-accent shadow-[0_0_12px_rgba(255,78,0,0.8)]" />
              <span className="text-white font-bold text-xl tracking-tight">Enma.</span>
            </div>
            <p className="text-white/50 text-sm leading-relaxed">Your AI Chief of Staff for CA Firms. Built for the future of professional services.</p>
            <div className="flex flex-col gap-1 mt-4">
              <p className="text-white/30 text-xs">© 2026 Enma Labs.</p>
              <p className="text-white/30 text-xs">Your clients&apos; data is never used for AI training.</p>
            </div>
          </div>

          {/* Column 2 */}
          <div className="flex flex-col gap-4">
            <h4 className="text-white font-bold text-[11px] uppercase tracking-widest mb-4">Product</h4>
            {['Features', 'Security', 'How It Works', 'Pricing'].map((item) => (
              <a
                key={item}
                href={`#${item.toLowerCase().replace(/ /g, '-')}`}
                className="text-white/50 hover:text-white text-sm transition-colors"
              >
                {item}
              </a>
            ))}
          </div>

          {/* Column 3 */}
          <div className="flex flex-col gap-4">
            <h4 className="text-white font-bold text-[11px] uppercase tracking-widest mb-4">Legal</h4>
            {['Privacy Policy', 'Terms of Service', 'DPA Summary'].map((item) => (
              <a
                key={item}
                href="#"
                className="text-white/50 hover:text-white text-sm transition-colors"
              >
                {item}
              </a>
            ))}
          </div>

          {/* Column 4 */}
          <div className="flex flex-col gap-4">
            <h4 className="text-white font-bold text-[11px] uppercase tracking-widest mb-4">Contact</h4>
            <a href="mailto:contact@enmalabs.com" className="text-white/50 hover:text-white text-sm transition-colors">
              contact@enmalabs.com
            </a>
            <Link href="/book-demo" className="text-white/50 hover:text-white text-sm transition-colors">
              Book a Demo
            </Link>
            <Link href="/login" className="text-white/50 hover:text-white text-sm transition-colors">
              Login
            </Link>
          </div>
        </div>

        {/* Bottom Bar */}
        <div className="pt-12 border-t border-white/5 flex flex-col md:flex-row justify-between items-center gap-4">
          <p className="text-white/20 text-[10px] uppercase tracking-[0.2em]">© 2026 Enma Labs.</p>
          <p className="text-white/20 text-[10px] uppercase tracking-[0.2em]">Made for CA Firms. Powered by AI.</p>
        </div>
      </div>
    </footer>
  );
}
