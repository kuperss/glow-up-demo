// === firebase-app.js ===
// 舞光戰將點燈計劃 · 共用 Firebase 模組
// 由所有 HTML 頁面 import 使用，提供 auth、progress、admin helper。

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.5/firebase-app.js";
import {
  getAuth, GoogleAuthProvider, signInWithPopup, signOut,
  onAuthStateChanged
} from "https://www.gstatic.com/firebasejs/10.12.5/firebase-auth.js";
import {
  getFirestore, doc, setDoc, getDoc, getDocs, collection, query, where,
  onSnapshot, serverTimestamp, deleteDoc, updateDoc
} from "https://www.gstatic.com/firebasejs/10.12.5/firebase-firestore.js";

const firebaseConfig = {
  apiKey: "AIzaSyDQZayWR7PzfvCjVIUKmXFnqcLQif7P3TE",
  authDomain: "dancelight-training.firebaseapp.com",
  projectId: "dancelight-training",
  storageBucket: "dancelight-training.firebasestorage.app",
  messagingSenderId: "552728708137",
  appId: "1:552728708137:web:67900fee79393a61b0c838"
};

// 超級管理員 — 第一次登入會自動建立 manager 帳號
// 想新增其他主管時，超管在 admin.html 把對方加進白名單並設 role=manager 即可
export const SUPER_ADMIN_EMAIL = "jerryloveyoux@gmail.com";

export const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const db = getFirestore(app);
const provider = new GoogleAuthProvider();

let currentUser = null;
let currentUserDoc = null;

// ========== 工具：把 email 轉成可作為 doc id 的字串 ==========
function emailKey(email) {
  return email.toLowerCase().replace(/[.#$/\[\]]/g, '_');
}

// ========== Auth 監聽 ==========
export function onAuth(callback) {
  return onAuthStateChanged(auth, async (user) => {
    if (user) {
      const profile = await ensureUserProfile(user);
      if (!profile.authorized) {
        await signOut(auth);
        currentUser = null;
        currentUserDoc = null;
        callback(null, { reason: profile.reason });
        return;
      }
      currentUser = user;
      currentUserDoc = profile.doc;
      callback(user, { doc: profile.doc });
    } else {
      currentUser = null;
      currentUserDoc = null;
      callback(null);
    }
  });
}

export async function signInWithGoogle() {
  return signInWithPopup(auth, provider);
}

export async function signOutUser() {
  await signOut(auth);
  location.href = 'login.html';
}

export function getCurrentUser() {
  return currentUser ? { auth: currentUser, doc: currentUserDoc } : null;
}

// ========== 確認用戶在白名單，沒有就拒絕；有就建/更 user doc ==========
async function ensureUserProfile(user) {
  const isSuper = user.email === SUPER_ADMIN_EMAIL;
  const userRef = doc(db, 'users', user.uid);

  // 先讀現有 user doc（已登入過的人）
  const userSnap = await getDoc(userRef);
  let profile;

  if (userSnap.exists()) {
    profile = userSnap.data();
    // 持續寫一筆 lastSeen 不會 fail（更新自己永遠 OK）
    try {
      await updateDoc(userRef, {
        lastSeen: serverTimestamp(),
        photoURL: user.photoURL || profile.photoURL || null
      });
    } catch(e) { /* ignore */ }
    return { authorized: true, doc: profile };
  }

  // 第一次登入：超管 OR 在 allowlist 才放行
  if (isSuper) {
    profile = {
      uid: user.uid,
      email: user.email,
      name: user.displayName || '超級管理員',
      empId: 'SUPER_ADMIN',
      role: 'manager',
      photoURL: user.photoURL || null,
      joinedAt: serverTimestamp(),
      lastSeen: serverTimestamp()
    };
    await setDoc(userRef, profile);
    return { authorized: true, doc: profile };
  }

  // 一般人：查 allowlist
  const allowRef = doc(db, 'allowlist', emailKey(user.email));
  const allowSnap = await getDoc(allowRef);
  if (!allowSnap.exists()) {
    return { authorized: false, reason: 'EMAIL_NOT_IN_ALLOWLIST' };
  }
  const allowData = allowSnap.data();

  profile = {
    uid: user.uid,
    email: user.email,
    name: allowData.name || user.displayName || user.email,
    empId: allowData.empId || '',
    role: allowData.role || 'trainee',
    photoURL: user.photoURL || null,
    joinedAt: serverTimestamp(),
    lastSeen: serverTimestamp()
  };
  await setDoc(userRef, profile);
  return { authorized: true, doc: profile };
}

// ========== 頁面守門員 ==========
export function requireAuth(opts = {}) {
  return new Promise((resolve) => {
    // ===== GUEST MODE (DEMO) — 正式上線前移除整個 if 區塊 =====
    if (sessionStorage.getItem('glow-guest-mode') === '1') {
      const guestDoc = {
        uid: 'guest', email: 'guest@demo', name: '訪客',
        empId: 'GUEST', role: opts.requireManager ? 'manager' : 'trainee',
        photoURL: null, isGuest: true
      };
      currentUser = { uid: 'guest', email: 'guest@demo' };
      currentUserDoc = guestDoc;
      // Stub Firestore helpers — in-memory only, no actual writes
      const guestProgress = { quizIds: new Set(), scenarios: new Set(), score: 0 };
      window.glowFirebase = {
        async recordQuizAttempt(quizId, isCorrect, points) {
          if (!guestProgress.quizIds.has(quizId)) {
            guestProgress.quizIds.add(quizId);
            if (isCorrect) guestProgress.score += points;
          }
        },
        async recordScenarioCompletion(key, points = 50) {
          if (!guestProgress.scenarios.has(key)) {
            guestProgress.scenarios.add(key);
            guestProgress.score += points;
          }
        },
        async loadProgress() {
          return {
            score: guestProgress.score,
            completed: guestProgress.quizIds.size + guestProgress.scenarios.size,
            quizIds: Array.from(guestProgress.quizIds),
            scenarios: Array.from(guestProgress.scenarios)
          };
        },
        async resetMyProgress() {
          guestProgress.quizIds.clear(); guestProgress.scenarios.clear(); guestProgress.score = 0;
        },
        getCurrentUser: () => ({ auth: currentUser, doc: currentUserDoc }),
        signOutUser: async () => {
          sessionStorage.removeItem('glow-guest-mode');
          location.href = 'login.html?signedout=1';
        },
        async listAllowlist() { return []; },
        async addToAllowlist() { alert('訪客模式：無法寫入授權白名單'); },
        async removeFromAllowlist() { alert('訪客模式：無法移除授權'); },
        async listAllUsers() { return [guestDoc]; },
        async getUserProgressDetail() {
          return { progress: [], scenarios: [], score: guestProgress.score, totalCompleted: guestProgress.quizIds.size + guestProgress.scenarios.size };
        },
        onUsersUpdate(cb) { cb([guestDoc]); return () => {}; },
        onAllowlistUpdate(cb) { cb([]); return () => {}; },
        async getStuckPointAnalysis() { return []; },
        async markWizardSeen() { guestDoc.wizardSeen = true; },
        async listSiteContent() { return []; },
        async saveContentValue() { alert('訪客模式：無法儲存內容變更'); },
        onSiteContentUpdate(cb) { cb([]); return () => {}; },
        async applySiteContent() { /* no-op for guest */ },
        async getCategoryMap() { return null; },
        async saveCategoryMap() { alert('訪客模式：無法儲存分類牆變更'); },
        onCategoryMapUpdate(cb) { cb(null); return () => {}; },
        renderCatIcon, CAT_ICONS, CAT_ICON_LABELS, CATEGORY_GROUPS, DEFAULT_CATEGORY_MAP,
        async getFlagshipProducts() { return null; },
        async saveFlagshipProducts() { alert('訪客模式：無法儲存主打產品變更'); },
        onFlagshipProductsUpdate(cb) { cb(null); return () => {}; },
        async generateFlagshipCard() { throw new Error('訪客模式：AI 功能不可用，請登入正式帳號'); },
        DEFAULT_FLAGSHIP_PRODUCTS, FLAGSHIP_TAG_PRESETS,
        async getAIConfig() { return { provider: 'gemini', apiKey: '', model: '', systemPrompt: '', enabled: false }; },
        async saveAIConfig() { alert('訪客模式：無法儲存 AI 設定'); },
        onAIConfigUpdate(cb) { cb({ provider: 'gemini', apiKey: '', model: '', systemPrompt: '', enabled: false }); return () => {}; },
        async callAI() { throw new Error('訪客模式：AI 功能不可用，請登入正式帳號'); },
        getProviderDefaults() { return PROVIDER_DEFAULTS; }
      };
      document.dispatchEvent(new CustomEvent('glow-firebase-ready', { detail: { user: currentUser, doc: guestDoc } }));
      resolve({ user: currentUser, doc: guestDoc });
      return;
    }
    // ===== /GUEST MODE =====

    onAuth((user, info) => {
      if (!user) {
        const reason = info && info.reason;
        if (reason === 'EMAIL_NOT_IN_ALLOWLIST') {
          location.href = 'login.html?notallowed=1';
        } else {
          // 避免 login.html 自己又跳到 login.html
          if (!location.pathname.endsWith('login.html')) {
            location.href = 'login.html';
          }
        }
        return;
      }
      // 角色限制（admin.html 用）
      if (opts.requireManager && currentUserDoc.role !== 'manager') {
        location.href = 'index.html?denied=manager';
        return;
      }
      // Expose Firestore helpers globally so non-module inline scripts can use them
      window.glowFirebase = {
        recordQuizAttempt, recordScenarioCompletion, loadProgress, resetMyProgress,
        getCurrentUser, signOutUser, markWizardSeen,
        listAllowlist, addToAllowlist, removeFromAllowlist,
        listAllUsers, getUserProgressDetail,
        onUsersUpdate, onAllowlistUpdate, getStuckPointAnalysis,
        listSiteContent, saveContentValue, onSiteContentUpdate, applySiteContent,
        getCategoryMap, saveCategoryMap, onCategoryMapUpdate,
        renderCatIcon, CAT_ICONS, CAT_ICON_LABELS, CATEGORY_GROUPS, DEFAULT_CATEGORY_MAP,
        getFlagshipProducts, saveFlagshipProducts, onFlagshipProductsUpdate, generateFlagshipCard,
        DEFAULT_FLAGSHIP_PRODUCTS, FLAGSHIP_TAG_PRESETS,
        getAIConfig, saveAIConfig, onAIConfigUpdate, callAI, getProviderDefaults,
        injectAIHelper
      };
      // 自動套用 siteContent 覆蓋
      applySiteContent();
      // 注入 AI 助教浮動按鈕（若有設定且 enabled）— admin 頁面不顯示（已有專屬 AI 設定 UI）
      if (!location.pathname.endsWith('admin.html')) {
        injectAIHelper();
      }
      // Dispatch ready event for inline scripts that need to wait
      document.dispatchEvent(new CustomEvent('glow-firebase-ready', {
        detail: { user, doc: currentUserDoc }
      }));
      resolve({ user, doc: currentUserDoc });
    });
  });
}

// ========== Quiz 進度寫入 ==========
export async function recordQuizAttempt(quizId, isCorrect, points) {
  if (!currentUser) return;
  const ref = doc(db, 'users', currentUser.uid, 'progress', quizId);
  await setDoc(ref, {
    quizId,
    correct: !!isCorrect,
    points: isCorrect ? points : 0,
    answeredAt: serverTimestamp()
  }, { merge: false });
}

export async function recordScenarioCompletion(scenarioKey, points = 50) {
  if (!currentUser) return;
  const ref = doc(db, 'users', currentUser.uid, 'scenarios', scenarioKey);
  await setDoc(ref, {
    scenarioKey,
    completed: true,
    points,
    completedAt: serverTimestamp()
  }, { merge: false });
}

export async function markWizardSeen() {
  if (!currentUser) return;
  try {
    await updateDoc(doc(db, 'users', currentUser.uid), { wizardSeen: true });
    if (currentUserDoc) currentUserDoc.wizardSeen = true;
  } catch(e) { console.warn('markWizardSeen failed', e); }
}

export async function resetMyProgress() {
  if (!currentUser) return;
  const uid = currentUser.uid;
  const [progSnap, scenSnap] = await Promise.all([
    getDocs(collection(db, 'users', uid, 'progress')),
    getDocs(collection(db, 'users', uid, 'scenarios'))
  ]);
  const dels = [];
  progSnap.forEach(d => dels.push(deleteDoc(d.ref)));
  scenSnap.forEach(d => dels.push(deleteDoc(d.ref)));
  await Promise.all(dels);
}

export async function loadProgress() {
  if (!currentUser) return null;
  const uid = currentUser.uid;
  const [progSnap, scenSnap] = await Promise.all([
    getDocs(collection(db, 'users', uid, 'progress')),
    getDocs(collection(db, 'users', uid, 'scenarios'))
  ]);
  const quizIds = [];
  let score = 0;
  progSnap.forEach(d => {
    const data = d.data();
    quizIds.push(data.quizId);
    score += data.points || 0;
  });
  const scenarios = [];
  scenSnap.forEach(d => {
    const data = d.data();
    scenarios.push(data.scenarioKey);
    score += data.points || 0;
  });
  return {
    score,
    completed: quizIds.length + scenarios.length,
    quizIds,
    scenarios
  };
}

// ========== Nav 上的用戶 pill ==========
export function injectUserPill() {
  if (!currentUserDoc) return;
  const nav = document.querySelector('nav');
  if (!nav) return;

  // nav 結構：左 LOGO + 中央 nav-link + 右側 (一個 link 或一個 user pill)
  // 找最右邊那個 child 替換掉
  const flexRow = nav.querySelector('.flex.items-center.justify-between, .max-w-7xl > .flex');
  if (!flexRow) return;

  // 移除舊的 pill（若有）
  const old = nav.querySelector('#userPillWrapper');
  if (old) old.remove();

  const lastChild = flexRow.lastElementChild;
  // 只替換在 LOGO 和中央 nav-link 之後的最右側元素（通常是「下一章 →」link 或主管後台 link）
  // 但不能替換中央 nav-link 容器
  if (lastChild && lastChild.classList.contains('flex') && lastChild.querySelectorAll('.nav-link').length > 0) {
    // 如果 last child 是中央 nav 容器，則 append 一個新的
    // 不替換，append
  }

  const wrapper = document.createElement('div');
  wrapper.id = 'userPillWrapper';
  wrapper.className = 'relative flex-shrink-0';
  const initial = (currentUserDoc.name || currentUserDoc.email || '?').charAt(0).toUpperCase();
  const isGuest = !!currentUserDoc.isGuest;
  wrapper.innerHTML = `
    <button id="userPillBtn" class="flex items-center gap-2 px-2 sm:px-3 py-1.5 rounded-full ${isGuest ? 'bg-gray-800/40 border border-gray-700' : 'bg-orange-500/10 border border-orange-500/30'} text-sm ${isGuest ? 'text-gray-300' : 'text-orange-200'} hover:bg-orange-500/15 transition-colors whitespace-nowrap max-w-[200px] sm:max-w-none">
      ${currentUserDoc.photoURL
        ? `<img src="${currentUserDoc.photoURL}" referrerpolicy="no-referrer" class="w-6 h-6 rounded-full flex-shrink-0" alt="">`
        : `<div class="w-6 h-6 rounded-full ${isGuest ? 'bg-gray-700' : 'bg-orange-500/30'} flex items-center justify-center text-xs font-bold flex-shrink-0">${initial}</div>`}
      <span class="hidden sm:inline truncate max-w-[120px]">${currentUserDoc.name || currentUserDoc.email}</span>
      ${isGuest ? '<span class="hidden sm:inline-block text-[10px] px-1.5 py-0.5 rounded bg-gray-700 ml-1">DEMO</span>' :
        (currentUserDoc.role === 'manager' ? '<span class="hidden sm:inline-block text-[10px] px-1.5 py-0.5 rounded bg-orange-500/25 ml-1">主管</span>' : '')}
    </button>
    <div id="userPillMenu" class="hidden absolute right-0 top-full mt-2 w-60 rounded-xl bg-[#131316] border border-orange-500/20 shadow-2xl shadow-orange-500/10 overflow-hidden z-50">
      <div class="px-4 py-3 border-b border-gray-800/60">
        <div class="text-sm font-semibold text-white">${currentUserDoc.name || ''}</div>
        ${currentUserDoc.empId ? `<div class="text-xs text-gray-500">員編 ${currentUserDoc.empId}</div>` : ''}
        <div class="text-xs text-gray-600 mt-1 truncate">${currentUserDoc.email}</div>
        ${isGuest ? '<div class="text-[10px] text-yellow-400 mt-1">⚠ 訪客模式 · 進度不會儲存</div>' :
          (currentUserDoc.role === 'manager' ? '<div class="text-[10px] text-orange-400 mt-1">★ 主管權限</div>' : '')}
      </div>
      ${(currentUserDoc.role === 'manager' || isGuest) ? '<a href="admin.html" class="block px-4 py-3 text-sm text-orange-300 hover:bg-orange-500/10 transition-colors border-b border-gray-800/60">主管後台 →</a>' : ''}
      <button id="signOutBtn" class="w-full px-4 py-3 text-left text-sm text-gray-300 hover:bg-orange-500/10 hover:text-orange-300 transition-colors">${isGuest ? '結束訪客模式' : '登出'}</button>
    </div>
  `;

  if (lastChild) {
    lastChild.replaceWith(wrapper);
  } else {
    flexRow.appendChild(wrapper);
  }

  document.getElementById('userPillBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    document.getElementById('userPillMenu').classList.toggle('hidden');
  });
  document.addEventListener('click', () => {
    const m = document.getElementById('userPillMenu');
    if (m) m.classList.add('hidden');
  });
  document.getElementById('signOutBtn').addEventListener('click', async () => {
    await signOutUser();
  });
}

// ========== Allowlist 管理（主管在 admin.html 用） ==========
export async function listAllowlist() {
  const snap = await getDocs(collection(db, 'allowlist'));
  const out = [];
  snap.forEach(d => out.push({ id: d.id, ...d.data() }));
  return out;
}

export async function addToAllowlist({ email, empId, name, role = 'trainee' }) {
  const key = emailKey(email);
  await setDoc(doc(db, 'allowlist', key), {
    email: email.toLowerCase(),
    empId: empId || '',
    name: name || '',
    role,
    addedAt: serverTimestamp()
  });
}

export async function removeFromAllowlist(allowKey) {
  await deleteDoc(doc(db, 'allowlist', allowKey));
}

// ========== 學員列表（主管讀全部） ==========
export async function listAllUsers() {
  const snap = await getDocs(collection(db, 'users'));
  const out = [];
  snap.forEach(d => out.push({ uid: d.id, ...d.data() }));
  return out;
}

// 即時訂閱：學員列表
export function onUsersUpdate(callback) {
  return onSnapshot(collection(db, 'users'), (snap) => {
    const list = [];
    snap.forEach(d => list.push({ uid: d.id, ...d.data() }));
    callback(list);
  }, (err) => console.warn('users onSnapshot err', err));
}

// 即時訂閱：白名單
export function onAllowlistUpdate(callback) {
  return onSnapshot(collection(db, 'allowlist'), (snap) => {
    const list = [];
    snap.forEach(d => list.push({ id: d.id, ...d.data() }));
    callback(list);
  }, (err) => console.warn('allowlist onSnapshot err', err));
}

// 卡關熱點：聚合所有用戶的 progress doc，計算每題答錯率
export async function getStuckPointAnalysis() {
  const usersSnap = await getDocs(collection(db, 'users'));
  const stats = {}; // quizId -> { attempts, wrong }
  // 並行抓每個用戶的 progress
  const promises = [];
  usersSnap.forEach(u => {
    promises.push(getDocs(collection(db, 'users', u.id, 'progress')).then(snap => {
      snap.forEach(p => {
        const data = p.data();
        const id = data.quizId;
        if (!stats[id]) stats[id] = { quizId: id, attempts: 0, wrong: 0 };
        stats[id].attempts++;
        if (!data.correct) stats[id].wrong++;
      });
    }));
  });
  await Promise.all(promises);
  return Object.values(stats).map(s => ({
    ...s,
    wrongRate: s.attempts > 0 ? s.wrong / s.attempts : 0
  })).sort((a, b) => (b.wrongRate - a.wrongRate) || (b.wrong - a.wrong));
}

export async function getUserProgressDetail(uid) {
  const [progSnap, scenSnap] = await Promise.all([
    getDocs(collection(db, 'users', uid, 'progress')),
    getDocs(collection(db, 'users', uid, 'scenarios'))
  ]);
  const progress = [];
  let score = 0;
  progSnap.forEach(d => { progress.push(d.data()); score += d.data().points || 0; });
  const scenarios = [];
  scenSnap.forEach(d => { scenarios.push(d.data()); score += d.data().points || 0; });
  return {
    progress, scenarios, score,
    quizCompleted: progress.length,
    scenariosCompleted: scenarios.length,
    totalCompleted: progress.length + scenarios.length
  };
}

// ========== 內容模組化（siteContent CMS）==========
// 在 HTML 中：<h1 data-content-id="home.hero.title">預設文字</h1>
// 主管在 admin.html 編輯後，所有頁面下次載入會抓到 Firestore 上的覆蓋值
// 若 Firestore 沒值就保留 HTML 原文（fallback 安全）

export async function listSiteContent() {
  const snap = await getDocs(collection(db, 'siteContent'));
  return snap.docs.map(d => ({ id: d.id, ...d.data() }));
}

export async function saveContentValue(id, value, meta = {}) {
  await setDoc(doc(db, 'siteContent', id), {
    id, value,
    page: meta.page || '',
    label: meta.label || '',
    type: meta.type || 'text',
    updatedAt: serverTimestamp()
  }, { merge: true });
}

export function onSiteContentUpdate(cb) {
  return onSnapshot(collection(db, 'siteContent'), snap => {
    const list = snap.docs.map(d => ({ id: d.id, ...d.data() }));
    cb(list);
  }, err => console.warn('siteContent onSnapshot err', err));
}

// 套用：把 Firestore 上的覆蓋值套到 [data-content-id] 元素
export async function applySiteContent() {
  const elements = document.querySelectorAll('[data-content-id]');
  if (elements.length === 0) return;
  try {
    const list = await listSiteContent();
    const map = {};
    list.forEach(item => { map[item.id] = item.value; });
    elements.forEach(el => {
      const id = el.dataset.contentId;
      if (map[id] != null && map[id] !== '') {
        const type = el.dataset.contentType || 'text';
        if (type === 'html') el.innerHTML = map[id];
        else el.textContent = map[id];
      }
    });
  } catch(e) { console.warn('applySiteContent failed', e); }
}

// ========== 產品分類牆 CMS（categoryMap）==========
// Firestore：siteContent/categoryMap = { type:'categoryMap', groups:[{code,label,cards:[{icon,title,subtitle}]}], updatedAt }
// 群組 code/label 固定（舞光官方 7 大應用場域），admin 只能改 cards 陣列。
// 前端 products.html 透過 onCategoryMapUpdate() 即時 render；無資料時 fallback 到 DEFAULT_CATEGORY_MAP。

export const CAT_ICONS = {
  'circle-target':  '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>',
  'circle-cross':   '<circle cx="12" cy="12" r="9"/><path d="M12 3v18M3 12h18"/>',
  'circle-line':    '<circle cx="12" cy="12" r="8"/><path d="M8 12h8"/>',
  'circle-double':  '<circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
  'circle-half':    '<circle cx="12" cy="12" r="9"/><path d="M12 3v18"/>',
  'shield-curve':   '<circle cx="12" cy="12" r="9"/><path d="M9 12a3 3 0 0 1 6 0"/>',
  'shield-check':   '<path d="M12 2v3M12 19v3M3 12h3M18 12h3"/><circle cx="12" cy="12" r="6"/><path d="M9 12l2 2 4-4"/>',
  'lines-vertical': '<path d="M3 12h18"/><path d="M5 8v8M9 6v12M13 8v8M17 6v12"/>',
  'house':          '<path d="M5 21v-9l7-9 7 9v9"/><path d="M9 21v-6h6v6"/>',
  'wall-light':     '<path d="M5 21v-9l7-9 7 9v9"/><circle cx="12" cy="14" r="2"/>',
  'bulb':           '<path d="M12 2v8"/><path d="M5 12a7 7 0 0 0 14 0"/><circle cx="12" cy="20" r="2"/>',
  'track':          '<rect x="3" y="9" width="18" height="3" rx="1.5"/><path d="M8 12v6M16 12v6"/>',
  'spotlight':      '<circle cx="12" cy="6" r="3"/><path d="M5 22l7-13 7 13"/>',
  'square-grid':    '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/>',
  'rect-grid':      '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 12h18M9 6v12M15 6v12"/>',
  'rect-lines':     '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 9h18M3 12h18M3 15h18"/>',
  'rect-board':     '<rect x="2" y="6" width="20" height="9" rx="1"/><path d="M12 18v3"/>',
  'tube':           '<rect x="3" y="10" width="18" height="4" rx="2"/>',
  'desk-lamp':      '<path d="M12 2v6M12 16v6M2 12h6M16 12h6"/><circle cx="12" cy="12" r="3"/>',
  'bolt':           '<path d="M12 2L8 8h8l-4 6"/><path d="M12 14v8"/>',
  'building':       '<path d="M12 2L4 8v12h16V8z"/><path d="M9 22v-6h6v6"/>',
  'factory':        '<path d="M2 22V8l10-6 10 6v14"/><path d="M6 22V12h12v10"/>',
  'damp-box':       '<rect x="6" y="8" width="12" height="8" rx="2"/><path d="M9 4v4M15 4v4"/>',
  'uv':             '<circle cx="12" cy="8" r="4"/><path d="M12 12v6M9 18h6"/>',
  'x-circle':       '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
  'phone':          '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>',
  'globe':          '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a9 9 0 0 1 0 18M12 3a9 9 0 0 0 0 18"/>',
  'sun':            '<circle cx="12" cy="12" r="6"/><path d="M12 4v2M12 18v2M4 12h2M18 12h2"/>',
  'street-light':   '<path d="M12 22V8M5 14l7-6 7 6M3 22h18"/>',
  'stairs':         '<path d="M12 22v-8M8 14l4-4 4 4"/><path d="M3 22h18"/>',
  'tree':           '<path d="M12 2v8M9 6l3-3 3 3M5 22c0-4 4-7 7-7s7 3 7 7"/>'
};

export const CAT_ICON_LABELS = {
  'circle-target':'崁燈／同心圓','circle-cross':'吸頂十字','circle-line':'環形橫線',
  'circle-double':'筒燈雙圓','circle-half':'半圓分割','shield-curve':'防眩崁燈',
  'shield-check':'盾牌打勾／防爆','lines-vertical':'軟條／格柵','house':'住家／壁燈',
  'wall-light':'壁燈含燈頭','bulb':'燈泡','track':'軌道條','spotlight':'投射錐光',
  'square-grid':'方格／線條燈','rect-grid':'輕鋼架','rect-lines':'格柵橫線',
  'rect-board':'黑板／看板燈','tube':'日光管','desk-lamp':'檯燈／太陽',
  'bolt':'閃電／緊急照明','building':'學校／建築','factory':'工廠廠房',
  'damp-box':'防潮燈','uv':'UV 殺菌','x-circle':'交叉／滅蚊',
  'phone':'手機／APP','globe':'地球／全球','sun':'太陽／泛光',
  'street-light':'路燈','stairs':'階梯','tree':'樹木／景觀'
};

export function renderCatIcon(name) {
  const inner = CAT_ICONS[name] || CAT_ICONS['circle-target'];
  return `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${inner}</svg>`;
}

// 七大應用場域固定群組碼（不可由 admin 改名）
export const CATEGORY_GROUPS = [
  { code: 'HOME',        label: '居家空間' },
  { code: 'COMMERCIAL',  label: '商業空間' },
  { code: 'OFFICE',      label: '辦公空間' },
  { code: 'SCHOOL',      label: '學校照明' },
  { code: 'INDUSTRIAL',  label: '工廠照明' },
  { code: 'DIGITAL',     label: '互聯數位' },
  { code: 'OUTDOOR',     label: '戶外空間' }
];

export const DEFAULT_CATEGORY_MAP = {
  groups: [
    { code:'HOME', label:'居家空間', cards: [
      { icon:'circle-target',  title:'崁燈',     subtitle:'索爾 · 奧丁 · 馬爾' },
      { icon:'circle-cross',   title:'吸頂燈',   subtitle:'雲朵 · 星鑽 · 全光譜' },
      { icon:'lines-vertical', title:'軟條燈',   subtitle:'COB · 鋁槽配件' },
      { icon:'house',          title:'壁燈',     subtitle:'玄關 · 床頭裝飾' },
      { icon:'bulb',           title:'光源類',   subtitle:'燈泡 · 燈管' }
    ]},
    { code:'COMMERCIAL', label:'商業空間', cards: [
      { icon:'track',         title:'軌道燈',     subtitle:'拉斐爾 · 達文西' },
      { icon:'spotlight',     title:'投射燈',     subtitle:'服飾 · 餐廳重點打燈' },
      { icon:'circle-double', title:'筒燈',       subtitle:'商空主流光源' },
      { icon:'shield-curve',  title:'防眩崁燈',   subtitle:'馬爾 · UGR < 19' },
      { icon:'square-grid',   title:'線條燈',     subtitle:'展示櫃 · 招牌' }
    ]},
    { code:'OFFICE', label:'辦公空間', cards: [
      { icon:'rect-grid',  title:'輕鋼架平板', subtitle:'辦公主力光源' },
      { icon:'rect-lines', title:'格柵燈',     subtitle:'UGR < 19 防眩' },
      { icon:'tube',       title:'日光燈具',   subtitle:'T5 · T8 · 經典款' },
      { icon:'desk-lamp',  title:'護眼檯燈',   subtitle:'個人桌 · 全光譜' },
      { icon:'bolt',       title:'緊急照明',   subtitle:'消防驗收必備' }
    ]},
    { code:'SCHOOL', label:'學校照明', cards: [
      { icon:'rect-grid',   title:'護眼平板燈',     subtitle:'教室主光源' },
      { icon:'rect-board',  title:'黑板燈',         subtitle:'板書照明專用' },
      { icon:'circle-line', title:'走廊吸頂',       subtitle:'通道 · 玄關' },
      { icon:'building',    title:'體育館高天井',   subtitle:'挑高大空間' },
      { icon:'house',       title:'宿舍壁燈',       subtitle:'床頭 · 走道' }
    ]},
    { code:'INDUSTRIAL', label:'工廠照明', cards: [
      { icon:'factory',      title:'高天井燈',  subtitle:'廠房 · 倉儲' },
      { icon:'damp-box',     title:'防潮燈',    subtitle:'食品廠 · 停車場' },
      { icon:'shield-check', title:'防爆燈',    subtitle:'化工 · 油氣區' },
      { icon:'uv',           title:'殺菌燈',    subtitle:'UV 紫外線' },
      { icon:'x-circle',     title:'滅蚊燈',    subtitle:'餐廳 · 廚房' }
    ]},
    { code:'DIGITAL', label:'互聯數位', cards: [
      { icon:'circle-target',  title:'Ai 智慧崁燈',  subtitle:'語音 · APP 控制' },
      { icon:'circle-half',    title:'智能吸頂',     subtitle:'智能雲朵 · 多情境' },
      { icon:'lines-vertical', title:'智控軟條',     subtitle:'RGB · 氛圍' },
      { icon:'phone',          title:'米家生態',     subtitle:'小米全屋 · APP' },
      { icon:'globe',          title:'Google Home',  subtitle:'語音串接' }
    ]},
    { code:'OUTDOOR', label:'戶外空間', cards: [
      { icon:'sun',          title:'泛光燈',       subtitle:'宙斯 · 阿波羅' },
      { icon:'street-light', title:'高燈路燈',     subtitle:'街道 · 廣場' },
      { icon:'stairs',       title:'階梯地底燈',   subtitle:'指引 · 嵌地' },
      { icon:'tree',         title:'草皮 · 照樹',  subtitle:'景觀 · 庭園' },
      { icon:'wall-light',   title:'戶外壁燈',     subtitle:'玄關 · 門口' }
    ]}
  ]
};

export async function getCategoryMap() {
  try {
    const snap = await getDoc(doc(db, 'siteContent', 'categoryMap'));
    if (snap.exists()) return snap.data();
  } catch(e) { console.warn('getCategoryMap failed', e); }
  return null;
}

export async function saveCategoryMap(data) {
  await setDoc(doc(db, 'siteContent', 'categoryMap'), {
    type: 'categoryMap',
    groups: data.groups || [],
    updatedAt: serverTimestamp(),
    updatedBy: (auth.currentUser && auth.currentUser.email) || 'unknown'
  });
}

export function onCategoryMapUpdate(cb) {
  return onSnapshot(doc(db, 'siteContent', 'categoryMap'), snap => {
    cb(snap.exists() ? snap.data() : null);
  }, err => console.warn('categoryMap onSnapshot err', err));
}

// ========== 9 必懂主打 CMS（flagshipProducts）==========
// 每張卡片儲存 sku（對應 products.json）+ 全部展示文案 + 規格表
// AI 生成功能：給定 SKU，呼叫 LLM 自動生成卡片內容（tag/eyebrow/title/blurb/pillText/pitch）
// admin 可以從 1,359 SKU 搜尋換掉任一張，前端 products.html 即時同步

export const FLAGSHIP_TAG_PRESETS = [
  '旗艦．FLAGSHIP', '走量．VOLUME', '商業．COMMERCIAL', '舒眩．COMFORT',
  '戶外．OUTDOOR', '重型．INDUSTRIAL', '氛圍．AMBIENCE', '辦公．OFFICE',
  '支架．BRACKET', '互聯．DIGITAL', '學校．SCHOOL', '工廠．FACTORY'
];

export const DEFAULT_FLAGSHIP_PRODUCTS = {
  cards: [
    {
      sku: 'D-UTMTTR8N', categories: ['商業'],
      tag: '旗艦．FLAGSHIP', eyebrow: 'MAGNETIC TRACK',
      title: '拉斐爾<br>超薄磁吸軌道', shortName: '拉斐爾 超薄磁吸軌道',
      blurb: '6 公釐超薄、不開槽即可安裝。磁吸結構讓燈具角度雙手就能調，是設計師最愛的「商業空間神器」。',
      pillText: 'DC 24V 系統', pitch: '「設計師不用拆天花，6 釐米直接貼」',
      specs: [
        { label: '瓦數', value: '8W' }, { label: '色溫', value: '4000K' },
        { label: '光通量', value: '600 lm' }, { label: '演色性 Ra', value: '90；R9 >50' },
        { label: '光束角', value: '36°' }, { label: '尺寸', value: '直徑44*寬120*長161mm' },
        { label: '壽命', value: '15000小時' }
      ]
    },
    {
      sku: 'D-21DOP25NR2', categories: ['居家'],
      tag: '走量．VOLUME', eyebrow: 'SOL DOWNLIGHT',
      title: '索爾<br>崁燈', shortName: '索爾 崁燈',
      blurb: '從 9 到 21 公分多種尺寸，一體式設計、護眼、好施工。電料行最常推薦的吸光主力。',
      pillText: '電料行最愛', pitch: '「九成案場都用得上的安全牌」',
      specs: [
        { label: '瓦數', value: '25W' }, { label: '色溫', value: '4000K' },
        { label: '光通量', value: '2500 lm' }, { label: '演色性 Ra', value: '≧ 80' },
        { label: '光束角', value: '120 °' }, { label: '尺寸', value: '直徑245*高100 mm' },
        { label: '壽命', value: '15000小時' }
      ]
    },
    {
      sku: 'D-15DOO12NR3', categories: ['居家', '商業'],
      tag: '商業．COMMERCIAL', eyebrow: 'ODIN DOWNLIGHT',
      title: '奧丁<br>極簡崁燈', shortName: '奧丁 極簡崁燈',
      blurb: '7.5 / 9 / 11 / 15 公分極簡無框。隱形等級設計，連接縫都看不到，商空、精品店、設計師最愛。',
      pillText: '極簡無邊框', pitch: '「天花板上看不到燈，只看到光」',
      specs: [
        { label: '瓦數', value: '12W' }, { label: '色溫', value: '4000K' },
        { label: '光通量', value: '1200 lm' }, { label: '演色性 Ra', value: '80' },
        { label: '光束角', value: '140°' }, { label: '尺寸', value: '直徑170*高28 mm' },
        { label: '壽命', value: '15000小時' }
      ]
    },
    {
      sku: 'D-7DOR12N', categories: ['居家'],
      tag: '舒眩．COMFORT', eyebrow: 'MARS ANTI-GLARE',
      title: '馬爾<br>防眩崁燈', shortName: '馬爾 防眩崁燈',
      blurb: '深防眩設計、UGR 低、光源完全藏進燈杯。父母長輩、書房、小孩房閱讀的最佳選擇。',
      pillText: 'UGR 防眩', pitch: '「給最在意眼睛的長輩用」',
      specs: [
        { label: '瓦數', value: '12W' }, { label: '色溫', value: '4000K' },
        { label: '光通量', value: '890 lm' }, { label: '演色性 Ra', value: '90' },
        { label: '光束角', value: '30°' }, { label: '尺寸', value: '直徑81*高82 mm' },
        { label: '壽命', value: '15000小時' }
      ]
    },
    {
      sku: 'OD-FLZ20DR1', categories: ['戶外'],
      tag: '戶外．OUTDOOR', eyebrow: 'ZEUS FLOOD',
      title: '宙斯<br>戶外泛光燈', shortName: '宙斯 戶外泛光燈',
      blurb: '10 / 20 / 50W 多瓦數選擇，IP66 戶外等級，招牌、廣告燈箱、庭院投射首選。',
      pillText: 'IP66 戶外', pitch: '「IP66 規格直接報，工程商秒懂」',
      specs: [
        { label: '瓦數', value: '20W' }, { label: '色溫', value: '6500K' },
        { label: '光通量', value: '2000 lm' }, { label: '演色性 Ra', value: '80' },
        { label: '光束角', value: '140°' }, { label: '防護等級', value: 'IP66' },
        { label: '尺寸', value: '長190*寬48*高225 mm' }
      ]
    },
    {
      sku: 'E-FLDB100D/2R2', categories: ['戶外', '工廠'],
      tag: '重型．INDUSTRIAL', eyebrow: 'APOLLO INDUSTRIAL',
      title: '阿波羅<br>大瓦數泛光燈', shortName: '阿波羅 大瓦數泛光燈',
      blurb: '100 / 150 / 200W 大瓦數泛光，廠房、停車場、球場、大型外牆投射的主力。',
      pillText: '工業重型', pitch: '「廠房 6 公尺挑高，一支阿波羅打到底」',
      specs: [
        { label: '瓦數', value: '100W' }, { label: '色溫', value: '5700K' },
        { label: '光通量', value: '11500 lm' }, { label: '演色性 Ra', value: '70' },
        { label: '光束角', value: '150°' }, { label: '防護等級', value: 'IP66' },
        { label: '尺寸', value: '長262*寬47*高262 mm' }
      ]
    },
    {
      sku: 'D-35NA24V-RGBDW', categories: ['互聯', '商業'],
      tag: '氛圍．AMBIENCE', eyebrow: 'DANCE COLOR',
      title: '舞色<br>幻彩軟條燈', shortName: '舞色 幻彩軟條燈',
      blurb: '5 米 RGB+DW 全彩，2700~6500K 調色，遙控+APP 雙控。網美店、餐酒館、咖啡廳氛圍救星。',
      pillText: 'RGB+DW 全彩', pitch: '「老闆，氛圍不到位是因為沒有 RGB」',
      specs: [
        { label: '瓦數', value: '8W（每米）' }, { label: '色溫', value: '2700~6500K、RGB' },
        { label: '光通量', value: '800 lm/米' }, { label: '演色性 Ra', value: '80' },
        { label: '尺寸', value: '長5000*寬10*高1.3 mm' }, { label: '壽命', value: '15000小時' }
      ]
    },
    {
      sku: 'D-PD40NR7', categories: ['辦公', '學校'],
      tag: '辦公．OFFICE', eyebrow: 'PANEL LIGHT',
      title: '柔光平板燈<br>2×2 尺', shortName: '柔光平板燈 2×2 尺',
      blurb: '辦公室、會議室、學校、診所主力光源。一片光均勻，不刺眼。多瓦數可選。',
      pillText: '辦公主力', pitch: '「辦公室一場下來，平板燈最划算」',
      specs: [
        { label: '瓦數', value: '40W' }, { label: '色溫', value: '4000K' },
        { label: '光通量', value: '4000 lm' }, { label: '演色性 Ra', value: '80' },
        { label: '光束角', value: '160°' }, { label: '尺寸', value: '長600*寬600*高32 mm' },
        { label: '壽命', value: '15000小時' }
      ]
    },
    {
      sku: 'D-T5BA1-NR10', categories: ['辦公', '居家'],
      tag: '支架．BRACKET', eyebrow: 'T5 STRIP',
      title: 'T5 一體式<br>支架燈', shortName: 'T5 一體式 支架燈',
      blurb: '1 / 2 / 3 / 4 尺四種長度，廚房、工作檯、層板、櫥櫃下最好施工的補光神器。',
      pillText: '走量神器', pitch: '「廚房層板補光，T5 一體式裝完就完事」',
      specs: [
        { label: '瓦數', value: '5W' }, { label: '色溫', value: '4000K' },
        { label: '光通量', value: '575 lm' }, { label: '演色性 Ra', value: '80' },
        { label: '光束角', value: '210°' }, { label: '尺寸', value: '長312*寬26*高37 mm' },
        { label: '壽命', value: '15000小時' }
      ]
    }
  ]
};

export async function getFlagshipProducts() {
  try {
    const snap = await getDoc(doc(db, 'siteContent', 'flagshipProducts'));
    if (snap.exists()) return snap.data();
  } catch(e) { console.warn('getFlagshipProducts failed', e); }
  return null;
}

export async function saveFlagshipProducts(data) {
  await setDoc(doc(db, 'siteContent', 'flagshipProducts'), {
    type: 'flagshipProducts',
    cards: data.cards || [],
    updatedAt: serverTimestamp(),
    updatedBy: (auth.currentUser && auth.currentUser.email) || 'unknown'
  });
}

export function onFlagshipProductsUpdate(cb) {
  return onSnapshot(doc(db, 'siteContent', 'flagshipProducts'), snap => {
    cb(snap.exists() ? snap.data() : null);
  }, err => console.warn('flagshipProducts onSnapshot err', err));
}

// AI 生成主打卡內容：給定 products.json 的單筆產品資料，回傳 {tag,eyebrow,title,blurb,pillText,pitch,categories}
export async function generateFlagshipCard(productData) {
  const prompt = `你是舞光業務新人訓練系統的內容編輯助手。給定下面這支產品的完整規格資料，生成一張「9 必懂主打」翻面卡片的文案內容。

產品資料：
商品名稱：${productData['商品名稱'] || ''}
產品型號：${productData['產品型號'] || ''}
類型：${productData['類型'] || ''}
瓦數：${productData['消耗電力'] || ''}
色溫：${productData['色溫'] || ''}
光通量：${productData['光通量'] || ''}
演色性：${productData['演色性'] || ''}
適用場景：${Array.isArray(productData['適用場景']) ? productData['適用場景'].join('、') : ''}
使用用途：${Array.isArray(productData['使用用途']) ? productData['使用用途'].join('、') : ''}
銷售切入點：${Array.isArray(productData['銷售切入點']) ? productData['銷售切入點'].join('、') : ''}
建議客群：${Array.isArray(productData['建議客群']) ? productData['建議客群'].join('、') : ''}

請依下列規範產出結果，**只回 JSON、不要 markdown code block、不要任何前後說明**：
{
  "tag": "情境標籤（XX．ENGLISH 格式，例：旗艦．FLAGSHIP / 商業．COMMERCIAL / 走量．VOLUME / 戶外．OUTDOOR / 辦公．OFFICE / 重型．INDUSTRIAL / 氛圍．AMBIENCE / 舒眩．COMFORT / 支架．BRACKET）",
  "eyebrow": "英文短名 / 全大寫 / 1-3 字（例：MAGNETIC TRACK、SOL DOWNLIGHT、PANEL LIGHT）",
  "title": "中文短名稱 / 中間用 <br> 換行 / 不超過 12 字（例：拉斐爾<br>超薄磁吸軌道）",
  "shortName": "中文短名稱 不換行版（例：拉斐爾 超薄磁吸軌道）",
  "blurb": "2-3 句口語業務介紹，強調賣點與情境，60~80 字",
  "pillText": "前面卡角落賣點關鍵字 / 6 字內（例：商業空間神器 / 電料行最愛 / IP66 戶外）",
  "pitch": "業務記憶話術 / 引號包起來 / 不超過 30 字（例：「設計師不用拆天花，6 釐米直接貼」）",
  "categories": ["從這 7 個選 1-2 個：居家、商業、辦公、學校、工廠、互聯、戶外"]
}`;

  const reply = await callAI({ messages: [{ role: 'user', content: prompt }] });
  // 嘗試解析 JSON（防 markdown 包裹）
  let cleaned = String(reply || '').trim();
  // 去除 ```json ... ``` 包裹
  cleaned = cleaned.replace(/^```(?:json)?\s*/i, '').replace(/\s*```\s*$/i, '');
  const parsed = JSON.parse(cleaned);
  // 防呆：確保 categories 是陣列
  if (typeof parsed.categories === 'string') parsed.categories = [parsed.categories];
  if (!Array.isArray(parsed.categories)) parsed.categories = [];
  return parsed;
}

// ========== AI 浮動助教（學員端）==========
// 在所有訓練頁右下角插入「AI 助教」按鈕，點開即可發問
function injectAIHelperCSS() {
  if (document.getElementById('glow-ai-helper-css')) return;
  const s = document.createElement('style');
  s.id = 'glow-ai-helper-css';
  s.textContent = `
    .ai-helper-fab {
      position: fixed; right: 24px; bottom: 24px; z-index: 9998;
      width: 56px; height: 56px; border-radius: 50%;
      background: linear-gradient(135deg, #F58220, #C66510);
      border: 1px solid rgba(255,200,150,0.3);
      display: flex; align-items: center; justify-content: center;
      cursor: pointer; box-shadow: 0 12px 40px -8px rgba(245,130,32,0.6);
      transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      animation: ai-fab-pulse 3s ease-in-out infinite;
    }
    .ai-helper-fab:hover { transform: scale(1.08) translateY(-2px); }
    @keyframes ai-fab-pulse { 0%,100% { box-shadow: 0 12px 40px -8px rgba(245,130,32,0.6); } 50% { box-shadow: 0 16px 60px -8px rgba(245,130,32,0.9); } }
    .ai-helper-panel {
      position: fixed; right: 24px; bottom: 92px; z-index: 9998;
      width: 380px; max-width: calc(100vw - 32px);
      height: 540px; max-height: calc(100vh - 120px);
      background: linear-gradient(180deg, #131316, #0f0f12);
      border: 1px solid rgba(245,130,32,0.3); border-radius: 18px;
      box-shadow: 0 30px 80px -20px rgba(245,130,32,0.4);
      display: none; flex-direction: column; overflow: hidden;
      backdrop-filter: blur(20px);
    }
    .ai-helper-panel.open { display: flex; animation: ai-panel-in 0.3s cubic-bezier(0.16,1,0.3,1); }
    @keyframes ai-panel-in { from { opacity: 0; transform: translateY(20px) scale(0.96); } to { opacity: 1; transform: translateY(0) scale(1); } }
    .ai-panel-header {
      padding: 16px 18px; border-bottom: 1px solid rgba(255,255,255,0.06);
      display: flex; align-items: center; gap: 12px;
      background: linear-gradient(180deg, rgba(245,130,32,0.08), transparent);
    }
    .ai-panel-icon {
      width: 36px; height: 36px; border-radius: 50%;
      background: linear-gradient(135deg, #FFA050, #F58220);
      display: flex; align-items: center; justify-content: center;
    }
    .ai-panel-title { font-weight: 700; font-size: 15px; color: #F5F5F7; }
    .ai-panel-subtitle { font-size: 11px; color: #6B6B75; }
    .ai-panel-close { margin-left: auto; padding: 6px; border-radius: 6px; color: #6B6B75; cursor: pointer; transition: all 0.2s; }
    .ai-panel-close:hover { color: #F58220; background: rgba(245,130,32,0.1); }
    .ai-chat-list { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
    .ai-chat-bubble {
      max-width: 85%; padding: 10px 14px; border-radius: 14px;
      font-size: 13px; line-height: 1.55; white-space: pre-wrap; word-wrap: break-word;
    }
    .ai-chat-bubble.ai { background: rgba(38,38,44,0.8); border: 1px solid rgba(255,255,255,0.05); color: #F5F5F7; align-self: flex-start; border-bottom-left-radius: 4px; }
    .ai-chat-bubble.user { background: linear-gradient(135deg, rgba(245,130,32,0.18), rgba(245,130,32,0.1)); border: 1px solid rgba(245,130,32,0.3); color: #F5F5F7; align-self: flex-end; border-bottom-right-radius: 4px; }
    .ai-chat-bubble.thinking { animation: ai-bubble-pulse 1.4s ease-in-out infinite; color: #A0A0AB; }
    @keyframes ai-bubble-pulse { 0%,100% { opacity: 0.6; } 50% { opacity: 1; } }
    .ai-chat-error { background: rgba(255,82,82,0.08); border: 1px solid rgba(255,82,82,0.3); color: #FFB0B0; padding: 10px 14px; border-radius: 12px; font-size: 12px; align-self: stretch; }
    .ai-input-area { border-top: 1px solid rgba(255,255,255,0.06); padding: 12px; display: flex; gap: 8px; }
    .ai-input {
      flex: 1; padding: 10px 14px; border-radius: 10px;
      background: rgba(8,8,10,0.6); border: 1px solid rgba(255,255,255,0.08);
      color: #F5F5F7; font-size: 13px; resize: none;
      font-family: 'Noto Sans TC', sans-serif;
    }
    .ai-input:focus { outline: none; border-color: rgba(245,130,32,0.5); background: rgba(8,8,10,0.9); }
    .ai-send-btn {
      padding: 0 14px; border-radius: 10px;
      background: linear-gradient(135deg, #F58220, #C66510);
      color: white; font-size: 13px; font-weight: 600; cursor: pointer;
      border: none; transition: all 0.2s;
    }
    .ai-send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .ai-send-btn:not(:disabled):hover { transform: translateY(-1px); box-shadow: 0 6px 20px -4px rgba(245,130,32,0.5); }
    .ai-helper-suggestions { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 16px 12px; }
    .ai-helper-suggestion {
      font-size: 11px; padding: 5px 10px; border-radius: 999px;
      background: rgba(245,130,32,0.08); border: 1px solid rgba(245,130,32,0.2);
      color: #FFA050; cursor: pointer; transition: all 0.2s;
    }
    .ai-helper-suggestion:hover { background: rgba(245,130,32,0.15); border-color: rgba(245,130,32,0.4); }
    @media (max-width: 480px) {
      .ai-helper-panel { width: calc(100vw - 16px); right: 8px; bottom: 80px; height: 70vh; }
    }
  `;
  document.head.appendChild(s);
}

export async function injectAIHelper() {
  // 訪客模式不顯示
  if (currentUserDoc && currentUserDoc.isGuest) return;
  // 讀取設定
  const cfg = await getAIConfig();
  if (!cfg.enabled || !cfg.apiKey) return;
  // 已注入過就不重複
  if (document.getElementById('aiHelperFab')) return;
  injectAIHelperCSS();

  const fab = document.createElement('button');
  fab.id = 'aiHelperFab';
  fab.className = 'ai-helper-fab';
  fab.title = '舞光 AI 助教';
  fab.innerHTML = `
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 2a4 4 0 0 1 4 4v3h3a4 4 0 0 1 4 4v3"/>
      <path d="M21 16v3a4 4 0 0 1-4 4h-3"/>
      <path d="M14 23H6a4 4 0 0 1-4-4v-3"/>
      <path d="M2 13v-3a4 4 0 0 1 4-4h3"/>
      <circle cx="12" cy="13" r="2.5" fill="white"/>
    </svg>`;
  document.body.appendChild(fab);

  const panel = document.createElement('div');
  panel.id = 'aiHelperPanel';
  panel.className = 'ai-helper-panel';
  panel.innerHTML = `
    <div class="ai-panel-header">
      <div class="ai-panel-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="white"/></svg>
      </div>
      <div>
        <div class="ai-panel-title">舞光 AI 助教</div>
        <div class="ai-panel-subtitle">隨時問訓練內容相關的問題</div>
      </div>
      <button class="ai-panel-close" id="aiPanelCloseBtn" title="關閉">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="ai-chat-list" id="aiChatList">
      <div class="ai-chat-bubble ai">嗨！我是舞光 AI 助教。<br>有任何訓練內容（產品、客戶、規章、福利）的疑問都可以問我。<br><br>提示：點下方建議快速開始。</div>
    </div>
    <div class="ai-helper-suggestions" id="aiSuggestions">
      <button class="ai-helper-suggestion" data-q="Ra90 跟 Ra80 差在哪？">Ra90 跟 Ra80 差在哪？</button>
      <button class="ai-helper-suggestion" data-q="客戶嫌我們貴怎麼回？">客戶嫌我們貴怎麼回？</button>
      <button class="ai-helper-suggestion" data-q="三安福祉是什麼？">三安福祉是什麼？</button>
    </div>
    <div class="ai-input-area">
      <textarea id="aiHelperInput" class="ai-input" rows="1" placeholder="問點什麼吧⋯"></textarea>
      <button id="aiHelperSendBtn" class="ai-send-btn">送出</button>
    </div>
  `;
  document.body.appendChild(panel);

  const list = document.getElementById('aiChatList');
  const input = document.getElementById('aiHelperInput');
  const sendBtn = document.getElementById('aiHelperSendBtn');
  const closeBtn = document.getElementById('aiPanelCloseBtn');
  const suggestionsEl = document.getElementById('aiSuggestions');
  const history = [];

  fab.addEventListener('click', () => {
    panel.classList.toggle('open');
    if (panel.classList.contains('open')) setTimeout(() => input.focus(), 300);
  });
  closeBtn.addEventListener('click', () => panel.classList.remove('open'));

  async function send(text) {
    text = (text || '').trim();
    if (!text) return;
    // 隱藏建議區
    if (suggestionsEl) suggestionsEl.style.display = 'none';
    // 用戶氣泡
    const u = document.createElement('div');
    u.className = 'ai-chat-bubble user';
    u.textContent = text;
    list.appendChild(u);
    history.push({ role: 'user', content: text });
    // 思考中
    const thinking = document.createElement('div');
    thinking.className = 'ai-chat-bubble ai thinking';
    thinking.textContent = '思考中⋯';
    list.appendChild(thinking);
    list.scrollTop = list.scrollHeight;
    input.value = ''; input.style.height = 'auto';
    sendBtn.disabled = true;

    try {
      const reply = await callAI({
        messages: history,
        // system 由 aiConfig 提供
      });
      thinking.remove();
      const a = document.createElement('div');
      a.className = 'ai-chat-bubble ai';
      a.textContent = reply;
      list.appendChild(a);
      history.push({ role: 'assistant', content: reply });
    } catch (e) {
      thinking.remove();
      const err = document.createElement('div');
      err.className = 'ai-chat-error';
      err.textContent = '⚠ ' + (e.message || e);
      list.appendChild(err);
    } finally {
      list.scrollTop = list.scrollHeight;
      sendBtn.disabled = false;
      input.focus();
    }
  }

  sendBtn.addEventListener('click', () => send(input.value));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input.value);
    }
  });
  // 自動高度
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  });
  // 建議快速問題
  suggestionsEl.querySelectorAll('.ai-helper-suggestion').forEach(b => {
    b.addEventListener('click', () => send(b.dataset.q));
  });
}

// ========== AI 整合（Gemini / Claude / OpenAI）==========
// 設定儲存於 /aiConfig/default
// 主管在 admin.html 設定 provider + apiKey + model + systemPrompt
// 任何登入用戶都能讀（用來呼叫 AI），但只有主管能寫
//
// 安全性：API key 在 Firestore，所有登入用戶都讀得到。
// 風險：員工可能拷貝 key 自用。緩解：
//   1) 在 API 提供商設定使用上限
//   2) 定期更換 key
//   3) 升級為 Cloudflare Worker 代理（下階段）

const DEFAULT_AI_CONFIG = {
  provider: 'gemini',        // 'gemini' | 'claude' | 'openai'
  apiKey: '',
  model: '',                 // 空字串時使用 provider 預設
  systemPrompt: '你是舞光 LED 業務新人訓練系統的 AI 助教。請用繁體中文，以友善、專業的口吻回答業務新人關於展晟照明集團、舞光 LED 產品、客戶經營、業務技巧的問題。回答簡潔明確，避免冗長，每次最多 200 字。',
  enabled: false
};

const PROVIDER_DEFAULTS = {
  gemini: { model: 'gemini-1.5-flash', label: 'Google Gemini', testEndpoint: 'https://generativelanguage.googleapis.com/' },
  claude: { model: 'claude-haiku-4-5-20251001', label: 'Anthropic Claude', testEndpoint: 'https://api.anthropic.com/' },
  openai: { model: 'gpt-4o-mini', label: 'OpenAI GPT', testEndpoint: 'https://api.openai.com/' }
};

export function getProviderDefaults() {
  return PROVIDER_DEFAULTS;
}

export async function getAIConfig() {
  try {
    const snap = await getDoc(doc(db, 'aiConfig', 'default'));
    if (snap.exists()) return { ...DEFAULT_AI_CONFIG, ...snap.data() };
  } catch (e) { console.warn('getAIConfig failed', e); }
  return { ...DEFAULT_AI_CONFIG };
}

export async function saveAIConfig(cfg) {
  await setDoc(doc(db, 'aiConfig', 'default'), {
    ...cfg,
    updatedAt: serverTimestamp()
  }, { merge: true });
}

export function onAIConfigUpdate(cb) {
  return onSnapshot(doc(db, 'aiConfig', 'default'), (snap) => {
    cb(snap.exists() ? { ...DEFAULT_AI_CONFIG, ...snap.data() } : { ...DEFAULT_AI_CONFIG });
  }, (err) => console.warn('aiConfig onSnapshot err', err));
}

// 統一呼叫介面
// messages: [{ role: 'user' | 'assistant', content: string }, ...]
// system: 可選，會覆蓋 config 的 systemPrompt
export async function callAI({ messages, system, configOverride } = {}) {
  const cfg = configOverride || await getAIConfig();
  if (!cfg.apiKey) throw new Error('尚未設定 API Key（請主管至 admin.html → AI 設定）');
  const model = (cfg.model && cfg.model.trim()) || PROVIDER_DEFAULTS[cfg.provider].model;
  const sysPrompt = system || cfg.systemPrompt || '';

  if (cfg.provider === 'gemini') return _callGemini(cfg.apiKey, model, messages, sysPrompt);
  if (cfg.provider === 'claude') return _callClaude(cfg.apiKey, model, messages, sysPrompt);
  if (cfg.provider === 'openai') return _callOpenAI(cfg.apiKey, model, messages, sysPrompt);
  throw new Error('未知的 AI provider: ' + cfg.provider);
}

async function _callGemini(apiKey, model, messages, system) {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(apiKey)}`;
  const body = {
    contents: messages.map(m => ({
      role: m.role === 'assistant' ? 'model' : 'user',
      parts: [{ text: m.content }]
    }))
  };
  if (system) body.systemInstruction = { parts: [{ text: system }] };
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body)
  });
  const data = await r.json();
  if (!r.ok) throw new Error(`Gemini ${r.status}: ${data?.error?.message || 'unknown'}`);
  const text = data?.candidates?.[0]?.content?.parts?.[0]?.text;
  if (!text) throw new Error('Gemini 回應為空');
  return text;
}

async function _callClaude(apiKey, model, messages, system) {
  const r = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'anthropic-dangerous-direct-browser-access': 'true',
      'content-type': 'application/json'
    },
    body: JSON.stringify({
      model,
      max_tokens: 1024,
      ...(system ? { system } : {}),
      messages
    })
  });
  const data = await r.json();
  if (!r.ok) throw new Error(`Claude ${r.status}: ${data?.error?.message || 'unknown'}`);
  const text = data?.content?.[0]?.text;
  if (!text) throw new Error('Claude 回應為空');
  return text;
}

async function _callOpenAI(apiKey, model, messages, system) {
  const allMsgs = system ? [{ role: 'system', content: system }, ...messages] : messages;
  const r = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'content-type': 'application/json'
    },
    body: JSON.stringify({ model, messages: allMsgs })
  });
  const data = await r.json();
  if (!r.ok) throw new Error(`OpenAI ${r.status}: ${data?.error?.message || 'unknown'}`);
  const text = data?.choices?.[0]?.message?.content;
  if (!text) throw new Error('OpenAI 回應為空');
  return text;
}

// 重新匯出常用 Firestore primitives 供頁面直接使用
export {
  doc, setDoc, getDoc, getDocs, collection, query, where,
  onSnapshot, serverTimestamp, deleteDoc, updateDoc
};

// ========== 全域 Toast 工具（用於徽章解鎖、答對提示）==========
function injectToastCSS() {
  if (document.getElementById('glow-toast-css')) return;
  const s = document.createElement('style');
  s.id = 'glow-toast-css';
  s.textContent = `
    .glow-toast-container { position: fixed; top: 80px; right: 20px; z-index: 10000; pointer-events: none; display: flex; flex-direction: column; gap: 12px; max-width: calc(100vw - 40px); }
    .glow-toast {
      pointer-events: auto;
      min-width: 240px; max-width: 360px;
      padding: 14px 18px; border-radius: 12px;
      background: linear-gradient(180deg, rgba(26,26,31,0.96), rgba(19,19,22,0.96));
      border: 1px solid rgba(245,130,32,0.3); color: #F5F5F7;
      box-shadow: 0 20px 60px -10px rgba(245,130,32,0.4);
      backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
      transform: translateX(400px); opacity: 0;
      transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.3s;
      font-family: 'Noto Sans TC', 'Inter', sans-serif;
    }
    .glow-toast.show { transform: translateX(0); opacity: 1; }
    .glow-toast.exit { transform: translateX(400px); opacity: 0; }
    .glow-toast.toast-correct { border-color: rgba(94,234,150,0.4); }
    .glow-toast.toast-badge {
      border-color: rgba(245,130,32,0.7);
      background: linear-gradient(180deg, rgba(245,130,32,0.18), rgba(19,19,22,0.96));
      box-shadow: 0 30px 80px -10px rgba(245,130,32,0.6), 0 0 0 1px rgba(245,130,32,0.5) inset;
    }
    .glow-toast-title { font-weight: 700; margin-bottom: 4px; font-size: 15px; }
    .glow-toast-desc { font-size: 12px; color: #A0A0AB; line-height: 1.5; }
    .glow-toast.toast-badge .glow-toast-title { color: #FFD27A; font-size: 16px; }
    @keyframes glow-toast-pulse { 0%,100% { box-shadow: 0 30px 80px -10px rgba(245,130,32,0.6), 0 0 0 1px rgba(245,130,32,0.5) inset; } 50% { box-shadow: 0 30px 100px -10px rgba(245,130,32,0.9), 0 0 0 2px rgba(245,130,32,0.7) inset; } }
    .glow-toast.toast-badge { animation: glow-toast-pulse 1.6s ease-in-out infinite; }
  `;
  document.head.appendChild(s);
}

window.showToast = function(opts) {
  injectToastCSS();
  let container = document.getElementById('glow-toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'glow-toast-container';
    container.className = 'glow-toast-container';
    document.body.appendChild(container);
  }
  const t = document.createElement('div');
  const typeClass = opts.type === 'correct' ? ' toast-correct' : opts.type === 'badge' ? ' toast-badge' : '';
  t.className = 'glow-toast' + typeClass;
  const safeTitle = String(opts.title || '').replace(/[<>]/g, '');
  const safeDesc = String(opts.desc || '').replace(/[<>]/g, '');
  t.innerHTML = `
    <div class="glow-toast-title">${safeTitle}</div>
    ${safeDesc ? `<div class="glow-toast-desc">${safeDesc}</div>` : ''}
  `;
  container.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  const dur = opts.duration || (opts.type === 'badge' ? 4500 : 2500);
  setTimeout(() => {
    t.classList.add('exit');
    setTimeout(() => t.remove(), 400);
  }, dur);
};
