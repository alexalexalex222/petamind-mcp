"use client";

import { useState } from "react";
import Link from "next/link";

// --- Icons (Inline SVGs) ---
const SearchIcon = () => (
 <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
);
const MapPinIcon = () => (
 <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
);
const StarIcon = ({ filled }: { filled?: boolean }) => (
 <svg className={`w-4 h-4 ${filled ? "text-yellow-400 fill-current" : "text-slate-300"}`} viewBox="0 0 20 20" fill="currentColor"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" /></svg>
);
const ChevronDown = () => (
 <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
);
const CheckIcon = () => (
 <svg className="w-5 h-5 text-violet-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
);
const ActivityIcon = () => (
 <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
);
const ShieldIcon = () => (
 <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
);
const ZapIcon = () => (
 <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
);
const BoneIcon = () => (
 <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0016.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 004 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z" /></svg>
);

// --- Data ---
const LISTINGS = [
 { id: 1, name: "Northside Rehab Center", rating: 4.9, reviews: 128, location: "Lincoln Park", specialty: ["Sports Rehab", "Post-Op"], image: true },
 { id: 2, name: "Dr. Sarah Jenkins, PT", rating: 5.0, reviews: 84, location: "River North", specialty: ["Pelvic Floor", "Chronic Pain"], image: true },
 { id: 3, name: "Chicago Mobility Clinic", rating: 4.8, reviews: 210, location: "West Loop", specialty: ["Geriatric Care", "Balance"], image: true },
];

const CATEGORIES = [
 { name: "Sports Rehab", icon: <ZapIcon /> },
 { name: "Geriatric Care", icon: <BoneIcon /> },
 { name: "Post-Op Recovery", icon: <ActivityIcon /> },
 { name: "Chronic Pain", icon: <ShieldIcon /> },
];

export default function HomePage() {
 const [filterOpen, setFilterOpen] = useState(false);

 return (
 <div className="min-h-screen bg-slate-50 font-sans text-slate-900 selection:bg-violet-100 selection:text-violet-900">
 {/** Navbar */}
 <nav className="sticky top-0 z-50 bg-white/80 backdrop-blur-md border-b border-slate-100">
 <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
 <div className="flex items-center gap-2">
 <div className="w-8 h-8 bg-violet-600 rounded-lg flex items-center justify-center text-white font-bold">T</div>
 <span className="font-semibold text-lg tracking-tight text-slate-900">TheraFind Pro</span>
 </div>
 <div className="hidden md:flex items-center gap-6 text-sm font-medium text-slate-600">
 <Link href="#listings" className="hover:text-violet-600 transition-colors">Providers</Link>
 <Link href="#approach" className="hover:text-violet-600 transition-colors">Guide</Link>
 <Link href="#faq" className="hover:text-violet-600 transition-colors">FAQ</Link>
 </div>
 <Link href="#search" className="bg-violet-600 hover:bg-violet-700 text-white px-5 py-2 rounded-full text-sm font-medium transition-colors shadow-sm shadow-violet-200">
 Find a Therapist
 </Link>
 </div>
 </nav>

 {/* Hero Section */}
 <section className="relative pt-20 pb-32 overflow-hidden">
 <div className="absolute inset-0 bg-gradient-to-b from-violet-50/50 to-transparent pointer-events-none" />
 <div className="max-w-4xl mx-auto px-4 text-center relative z-10">
 <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-violet-100 text-violet-700 text-xs font-semibold mb-6">
 <span className="w-1.5 h-1.5 rounded-full bg-violet-600 animate-pulse" />
 Serving the Greater Chicago Area
 </div>
 <h1 className="text-4xl md:text-6xl font-extrabold tracking-tight text-slate-900 mb-6 leading-[1.1]">
 Recovery starts with the <br className="hidden md:block" /> <span className="text-violet-600">right expert</span>.
 </h1>
 <p className="text-lg md:text-xl text-slate-500 mb-10 max-w-2xl mx-auto leading-relaxed">
 A premium directory connecting patients with top-rated physical therapists. Verify credentials, compare ratings, and book your recovery journey today.
 </p>
 <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-12">
 <button className="w-full sm:w-auto px-8 py-3.5 bg-violet-600 hover:bg-violet-700 text-white rounded-full font-semibold transition-all shadow-lg shadow-violet-200 hover:shadow-xl hover:-translate-y-0.5">
 Browse All Providers
 </button>
 <button className="w-full sm:w-auto px-8 py-3.5 bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 rounded-full font-semibold transition-colors">
 Get Matched Instantly
 </button>
 </div>
 {/* Trust Chips */}
 <div className="flex flex-wrap items-center justify-center gap-6 text-sm text-slate-500 opacity-80">
 <span className="flex items-center gap-1.5"><CheckIcon /> Verified Credentials</span>
 <span className="flex items-center gap-1.5"><CheckIcon /> Transparent Pricing</span>
 <span className="flex items-center gap-1.5"><CheckIcon /> Real-Time Availability</span>
 </div>
 </div>
 </section>

 {/* Search & Filters (Sticky) */}
 <div id="search" className="sticky top-16 z-40 -mt-16 px-4">
 <div className="max-w-5xl mx-auto bg-white rounded-2xl shadow-xl shadow-slate-200/50 p-2 border border-slate-100">
 <div className="flex flex-col md:flex-row gap-2">
 <div className="flex-1 flex items-center gap-3 px-4 py-3 bg-slate-50 rounded-xl border border-transparent focus-within:border-violet-200 focus-within:bg-white focus-within:ring-2 ring-violet-100 transition-all">
 <SearchIcon />
 <input type="text" placeholder="Condition, treatment, or therapist name" className="bg-transparent w-full outline-none text-sm text-slate-900 placeholder:text-slate-400" />
 </div>
 <div className="flex items-center gap-2">
 <div className="hidden md:flex items-center gap-2 px-4 py-3 bg-slate-50 rounded-xl border border-slate-100 min-w-[160px]">
 <MapPinIcon />
 <span className="text-sm text-slate-900">Chicago, IL</span>
 <ChevronDown />
 </div>
 <button onClick={() => setFilterOpen(!filterOpen)} className="hidden md:flex items-center gap-2 px-4 py-3 bg-slate-50 rounded-xl border border-slate-100 hover:bg-slate-100 transition-colors">
 <span className="text-sm font-medium text-slate-700">Filters</span>
 <ChevronDown />
 </button>
 <button className="w-full md:w-auto px-6 py-3 bg-violet-600 hover:bg-violet-700 text-white rounded-xl font-medium transition-colors shadow-md">
 Search
 </button>
 </div>
 </div>
 
 {/* Mobile Filters Collapsible */}
 {filterOpen && (
 <div className="mt-4 pt-4 border-t border-slate-100 grid grid-cols-1 md:grid-cols-3 gap-4 animate-in fade-in slide-in-from-top-2 duration-300">
 <div className="space-y-2">
 <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Specialty</label>
 <select className="w-full p-2 rounded-lg bg-white border border-slate-200 text-sm text-slate-700 outline-none focus:border-violet-500">
 <option>All Specialties</option>
 <option>Sports Rehab</option>
 <option>Post-Op Recovery</option>
 </select>
 </div>
 <div className="space-y-2">
 <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Insurance</label>
 <div className="flex items-center gap-2 pt-2">
 <input type="checkbox" id="ins" className="w-4 h-4 rounded border-slate-300 text-violet-600 focus:ring-violet-500" />
 <label htmlFor="ins" className="text-sm text-slate-700">Accepts My Insurance</label>
 </div>
 </div>
 </div>
 )}
 </div>
 </div>

 {/* Main Content Grid */}
 <div className="max-w-7xl mx-auto px-4 py-20 grid grid-cols-1 lg:grid-cols-4 gap-12">
 {/* Sidebar (Desktop) / Empty (Mobile) */}
 <aside className="hidden lg:block space-y-8">
 <div className="sticky top-36">
 <h3 className="font-semibold text-slate-900 mb-4">Quick Categories</h3>
 <nav className="space-y-1">
 {CATEGORIES.map((cat) => (
 <button key={cat.name} className="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left text-slate-600 hover:bg-white hover:text-violet-600 hover:shadow-sm border border-transparent hover:border-slate-100 transition-all group">
 <span className="text-slate-400 group-hover:text-violet-500">{cat.icon}</span>
 <span className="text-sm font-medium">{cat.name}</span>
 </button>
 ))}
 </nav>
 </div>
 </aside>

 {/* Featured Listings */}
 <div id="listings" className="lg:col-span-3 space-y-8">
 <div className="flex items-center justify-between">
 <h2 className="text-2xl font-bold text-slate-900">Featured Providers</h2>
 <span className="text-sm text-slate-500">Showing 3 of 124</span>
 </div>

 <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
 {LISTINGS.map((listing) => (
 <div key={listing.id} className="group bg-white rounded-2xl p-4 border border-slate-100 shadow-sm hover:shadow-xl hover:-translate-y-1 transition-all duration-300">
 <div className="aspect-video rounded-xl bg-slate-100 mb-4 overflow-hidden relative">
 {/* Placeholder Image Pattern */}
 <div className="absolute inset-0 bg-gradient-to-tr from-violet-100 to-slate-50" />
 <div className="absolute inset-0 flex items-center justify-center text-slate-300 font-bold text-4xl opacity-20 select-none">
 {listing.name.charAt(0)}
 </div>
 <div className="absolute top-3 right-3 bg-white/90 backdrop-blur px-2 py-1 rounded-lg text-xs font-bold text-slate-800 shadow-sm flex items-center gap-1">
 <StarIcon filled /> {listing.rating}
 </div>
 </div>
 <div className="space-y-2">
 <h3 className="font-bold text-slate-900 group-hover:text-violet-700 transition-colors">{listing.name}</h3>
 <div className="flex items-center gap-1 text-xs text-slate-500">
 <MapPinIcon /> {listing.location}
 </div>
 <div className="flex flex-wrap gap-2 mt-3">
 {listing.specialty.map((tag) => (
 <span key={tag} className="px-2.5 py-1 bg-slate-50 text-slate-600 text-xs font-medium rounded-md border border-slate-100">
 {tag}
 </span>
 ))}
 </div>
 </div>
 <button className="w-full mt-4 py-2.5 border border-violet-100 text-violet-600 rounded-xl text-sm font-semibold hover:bg-violet-50 transition-colors">
 View Profile
 </button>
 </div>
 ))}
 </div>
 </div>
 </div>

 {/* Signature Moment: Comparison Strip */}
 <section id="approach" className="py-24 bg-white border-t border-slate-100 relative overflow-hidden">
 <div className="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAiIGhlaWdodD0iMjAiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PGNpcmNsZSBjeD0iMSIgY3k9IjEiIHI9IjEiIGZpbGw9IiNjY2MiIGZpbGwtb3BhY2l0eT0iMC4xIi8+PC9zdmc+')] opacity-30" />
 <div className="max-w-7xl mx-auto px-4 relative z-10">
 <div className="text-center max-w-2xl mx-auto mb-16">
 <h2 className="text-3xl font-bold text-slate-900 mb-4">Choosing Your Path to Recovery</h2>
 <p className="text-slate-500">Understand the different therapeutic approaches available in Chicago to make the best decision for your body.</p>
 </div>

 <div className="grid grid-cols-1 md:grid-cols-2 gap-0 md:gap-12 relative">
 {/* Connector Line for Desktop */}
 <div className="hidden md:block absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full h-1 border-t-2 border-dashed border-slate-200 z-0" />
 <div className="hidden md:block absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-8 h-8 bg-white border-2 border-violet-500 rounded-full z-10 flex items-center justify-center">
 <div className="w-3 h-3 bg-violet-500 rounded-full" />
 </div>

 {/* Option 1 */}
 <div className="bg-violet-50/50 rounded-3xl p-8 md:p-12 relative z-0 border border-violet-100">
 <div className="w-14 h-14 bg-white rounded-2xl flex items-center justify-center shadow-sm text-violet-600 mb-6">
 <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 11.5V14m0-2.5v-6a1.5 1.5 0 113 0m-3 6a1.5 1.5 0 00-3 0v2a7.5 7.5 0 0015 0v-5a1.5 1.5 0 00-3 0m-6-3V11m0-5.5v-1a1.5 1.5 0 013 0v1m0 0V11m0-5.5a1.5 1.5 0 013 0v3m0 0V11" /></svg>
 </div>
 <h3 className="text-2xl font-bold text-slate-900 mb-2">Manual Therapy</h3>
 <p className="text-slate-600 mb-6 leading-relaxed">
 Hands-on techniques to mobilize joints and soft tissues. Ideal for acute pain, stiffness, and restoring natural movement patterns.
 </p>
 <ul className="space-y-3">
 {["Myofascial Release", "Joint Mobilization", "Soft Tissue Massage"].map((item) => (
 <li key={item} className="flex items-center gap-3 text-sm font-medium text-slate-700">
 <span className="w-1.5 h-1.5 rounded-full bg-violet-500" /> {item}
 </li>
 ))}
 </ul>
 </div>

 {/* Option 2 */}
 <div className="bg-slate-50 rounded-3xl p-8 md:p-12 relative z-0 border border-slate-100">
 <div className="w-14 h-14 bg-white rounded-2xl flex items-center justify-center shadow-sm text-slate-700 mb-6">
 <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" /></svg>
 </div>
 <h3 className="text-2xl font-bold text-slate-900 mb-2">Tech-Assisted</h3>
 <p className="text-slate-600 mb-6 leading-relaxed">
 Data-driven recovery using advanced equipment. Best for precise strength training, gait analysis, and post-surgical protocol adherence.
 </p>
 <ul className="space-y-3">
 {["EMG Biofeedback", "AlterG Anti-Gravity", "Instrumented Assisted"].map((item) => (
 <li key={item} className="flex items-center gap-3 text-sm font-medium text-slate-700">
 <span className="w-1.5 h-1.5 rounded-full bg-slate-400" /> {item}
 </li>
 ))}
 </ul>
 </div>
 </div>
 </div>
 </section>

 {/* Simple FAQ (Accordion) */}
 <section id="faq" className="py-20 max-w-3xl mx-auto px-4">
 <h2 className="text-3xl font-bold text-slate-900 mb-10 text-center">Common Questions</h2>
 <div className="space-y-4">
 {[{q: "How do you verify therapist credentials?", a: "We cross-check state licenses, board certifications, and continuing education records monthly."},
 {q: "Can I book appointments directly?", a: "Yes, most listings offer direct online booking or secure request forms for same-day follow-up."},
 {q: "Is there a cost to use the directory?", a: "No, our service is completely free for patients. Providers pay a listing fee to be featured."}].map((item, i) => (
 <details key={i} className="group bg-white rounded-2xl border border-slate-200 overflow-hidden">
 <summary className="flex items-center justify-between p-6 cursor-pointer font-semibold text-slate-900 select-none hover:bg-slate-50 transition-colors">
 {item.q}
 <span className="transition-transform group-open:rotate-180"><ChevronDown /></span>
 </summary>
 <div className="px-6 pb-6 text-slate-600 leading-relaxed pt-0 animate-in fade-in slide-in-from-top-2">
 {item.a}
 </div>
 </details>
 ))}
 </div>
 </section>

 {/* Footer */}
 <footer className="bg-slate-900 text-slate-300 py-16">
 <div className="max-w-7xl mx-auto px-4 grid grid-cols-1 md:grid-cols-4 gap-12">
 <div className="space-y-4">
 <div className="flex items-center gap-2 text-white font-bold text-xl">
 <div className="w-8 h-8 bg-violet-600 rounded-lg flex items-center justify-center text-sm">T</div>
 TheraFind Pro
 </div>
 <p className="text-sm text-slate-400 leading-relaxed">
 Connecting Chicago residents with the highest standard of physical therapy care since 2024.
 </p>
 </div>
 <div>
 <h4 className="text-white font-semibold mb-4">Directory</h4>
 <ul className="space-y-2 text-sm">
 <li><a href="#" className="hover:text-violet-400 transition-colors">Search Providers</a></li>
 <li><a href="#" className="hover:text-violet-400 transition-colors">Specialties</a></li>
 <li><a href="#" className="hover:text-violet-400 transition-colors">Chicago Neighborhoods</a></li>
 <li><a href="#" className="hover:text-violet-400 transition-colors">Insurance Checker</a></li>
 </ul>
 </div>
 <div>
 <h4 className="text-white font-semibold mb-4">Company</h4>
 <ul className="space-y-2 text-sm">
 <li><a href="#" className="hover:text-violet-400 transition-colors">About Us</a></li>
 <li><a href="#" className="hover:text-violet-400 transition-colors">For Providers</a></li>
 <li><a href="#" className="hover:text-violet-400 transition-colors">Privacy Policy</a></li>
 <li><a href="#" className="hover:text-violet-400 transition-colors">Terms of Service</a></li>
 </ul>
 </div>
 <div>
 <h4 className="text-white font-semibold mb-4">Stay Updated</h4>
 <div className="flex gap-2">
 <input type="email" placeholder="Enter email" className="bg-slate-800 border-none rounded-lg px-4 py-2 w-full text-sm focus:ring-2 ring-violet-500 outline-none" />
 <button className="bg-violet-600 hover:bg-violet-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">Join</button>
 </div>
 <div className="flex gap-4 mt-6">
 {['twitter', 'facebook', 'instagram'].map((s) => (
 <a key={s} href="#" className="text-slate-400 hover:text-white transition-colors">
 <div className="w-5 h-5 bg-current rounded-full opacity-20 hover:opacity-100" />
 </a>
 ))}
 </div>
 </div>
 </div>
 <div className="max-w-7xl mx-auto px-4 mt-12 pt-8 border-t border-slate-800 text-center text-xs text-slate-500">
 &copy; 2024 TheraFind Pro. All rights reserved. Not medical advice.
 </div>
 </footer>
 </div>
 );
}
