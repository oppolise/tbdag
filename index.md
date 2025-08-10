# วิเคราะห์โค้ด CGS-DNN Analysis

เอกสารนี้จะอธิบายการทำงานของไฟล์ `index.html` ที่ใช้ในการแสดงผลการวิเคราะห์โปรไฟล์ของ CGS-DNN โดยจะแบ่งการอธิบายออกเป็นส่วนต่างๆ ดังนี้:
1.  โครงสร้างของโค้ด HTML
2.  คำอธิบายโค้dในส่วน `<script>` แบบบรรทัดต่อบรรทัด
3.  สรุปการทำงานของแต่ละโมดูลการแสดงผล (View)

---

## 1. โครงสร้างของโค้ด HTML

ไฟล์ `index.html` เป็นหน้าเว็บแบบ Single Page Application (SPA) ที่ใช้ในการแสดงข้อมูลการวิเคราะห์โปรไฟล์ ประกอบด้วยส่วนหลักๆ ดังนี้

*   **`<head>`**:
    *   `<meta charset="utf-8" />`: กำหนดชุดอักขระเป็น UTF-8 เพื่อให้แสดงภาษาไทยได้อย่างถูกต้อง
    *   `<meta name="viewport" ...>`: ตั้งค่าการแสดงผลสำหรับอุปกรณ์พกพา (Responsive Design)
    *   `<title>`: กำหนดชื่อของหน้าเว็บที่แสดงบนแท็บของเบราว์เซอร์
    *   `<script src="https://d3js.org/d3.v7.min.js"></script>`: นำเข้าไลบรารี D3.js เวอร์ชัน 7 ซึ่งเป็นเครื่องมือหลักในการสร้าง Data Visualization (กราฟและแผนภูมิต่างๆ)
    *   `<style>`: เป็นส่วนที่กำหนด CSS สำหรับตกแต่งหน้าเว็บทั้งหมด เช่น สี, layout, font, และสไตล์ของส่วนประกอบต่างๆ ของกราฟ

*   **`<body>`**: เป็นส่วนเนื้อหาของหน้าเว็บ ประกอบด้วย
    *   **`<div id="plugin-container">`**: เป็นคอนเทนเนอร์หลักที่ครอบคลุมทั้งแอปพลิเคชัน แบ่งออกเป็น 2 ส่วนย่อยคือ Sidebar และ Content Pane
        *   **`<div id="sidebar">`**: แถบควบคุมด้านซ้ายมือของผู้ใช้ ประกอบด้วย:
            *   `run-selector`: Dropdown สำหรับเลือก "Run" หรือชุดข้อมูลการทดลองที่ต้องการวิเคราะห์
            *   `worker-selector`: Dropdown สำหรับเลือก "Worker" หรือโหนดที่ต้องการดูข้อมูล
            *   `view-selector`: Dropdown สำหรับเลือกรูปแบบการแสดงผล มี 3 ตัวเลือกคือ DAG Graph, Communication Timing, และ Runtime
        *   **`<main id="content-pane">`**: พื้นที่แสดงผลหลักทางด้านขวา ประกอบด้วย:
            *   `content-title`: ส่วนหัวเรื่องที่บอกว่ากำลังแสดงผลข้อมูลอะไรอยู่
            *   `content-display`: พื้นที่หลักที่กราฟหรือตารางต่างๆ จะถูกวาดลงไป
    *   **`<div id="d3-tooltip">`**: เป็น `div` ที่ซ่อนไว้ ใช้สำหรับแสดงข้อมูลเพิ่มเติม (Tooltip) เมื่อผู้ใช้ชี้เมาส์ไปที่ส่วนต่างๆ ของกราฟที่สร้างด้วย D3.js
    *   **`<script>`**: ส่วนที่บรรจุโค้ด JavaScript ทั้งหมดที่ควบคุมการทำงานของหน้าเว็บนี้

---

## 2. คำอธิบายโค้ดในส่วน `<script>`

โค้ด JavaScript นี้จะเริ่มทำงานเมื่อโครงสร้าง HTML ของหน้าเว็บ (DOM) โหลดเสร็จสมบูรณ์แล้ว

```javascript
// รอให้ HTML โหลดเสร็จก่อนจึงจะเริ่มทำงาน
document.addEventListener('DOMContentLoaded', () => {
  // --- การประกาศตัวแปรและอ้างอิงถึง Element ใน HTML ---
  const runSelector = document.getElementById('run-selector');         // Dropdown สำหรับเลือก Run
  const workerSelector = document.getElementById('worker-selector');     // Dropdown สำหรับเลือก Worker
  const viewSelector = document.getElementById('view-selector');       // Dropdown สำหรับเลือก View
  const workerSelectorGroup = document.getElementById('worker-selector-group'); // กลุ่มของ Worker Dropdown (เพื่อซ่อน/แสดง)
  const contentTitle = document.getElementById('content-title');     // Element ที่แสดงหัวข้อของเนื้อหา
  const contentDisplay = document.getElementById('content-display');   // พื้นที่หลักสำหรับแสดงผลกราฟ/ตาราง
  const tooltip = d3.select('#d3-tooltip');                           // Element สำหรับแสดง Tooltip (จัดการผ่าน D3)

  // --- ตัวแปรสำหรับเก็บข้อมูลและสถานะของแอปพลิเคชัน ---
  let allRuns = [], workersByRun = {}, opTreeDataCache = {}, commTimingCache = null;
  // allRuns: เก็บรายชื่อของ Runs ทั้งหมด
  // workersByRun: เก็บรายชื่อ Workers โดยแยกตาม Run (เป็น Cache)
  // opTreeDataCache: เก็บข้อมูลของ DAG และ Runtime ที่โหลดมาแล้ว (เป็น Cache)
  // commTimingCache: เก็บข้อมูลของ Communication Timing ที่โหลดมาแล้ว (เป็น Cache)
  let currentState = { run: null, worker: null, view: 'runtime' };
  // currentState: เก็บสถานะปัจจุบันที่ผู้ใช้เลือก (run, worker, view)

  // --- การผูก Event Listeners เข้ากับ Dropdown ---
  runSelector.addEventListener('change', handleRunChange);       // เมื่อผู้ใช้เปลี่ยน Run
  workerSelector.addEventListener('change', handleWorkerChange); // เมื่อผู้ใช้เปลี่ยน Worker
  viewSelector.addEventListener('change', handleViewChange);   // เมื่อผู้ใช้เปลี่ยน View

  // --- ฟังก์ชันเริ่มต้นการทำงาน (Initialization) ---
  async function initialize() {
    try {
      // 1. โหลดรายชื่อ Runs ทั้งหมดจาก Server
      const res = await fetch('./runs');
      const d = await res.json();
      allRuns = d.runs || [];
      // 2. นำรายชื่อ Runs ที่ได้ไปใส่ใน Dropdown
      populateRunsSelector(allRuns);
      // 3. ถ้ามี Run ให้เลือก Run แรกเป็นค่าเริ่มต้น และโหลดข้อมูล Worker ต่อ
      if (allRuns.length) {
        runSelector.selectedIndex = 0;
        await handleRunChange();
      } else {
        // ถ้าไม่มี Run เลย ให้แสดงข้อความแจ้งผู้ใช้
        runSelector.innerHTML = '<option>No runs found</option>';
        workerSelector.innerHTML = '<option>No runs found</option>';
        contentTitle.textContent = 'No profiler data found.';
      }
    } catch (err) {
      // กรณีเกิดข้อผิดพลาดในการโหลดข้อมูลเริ่มต้น
      contentTitle.textContent = `Error loading initial data: ${err.message}`;
    }
  }

  // ฟังก์ชันสำหรับเติมข้อมูลลงใน Run Dropdown
  function populateRunsSelector(names) {
    runSelector.innerHTML = ''; // ล้างค่าเก่าทิ้ง
    names.forEach(n => {
      const o = document.createElement('option');
      o.value = n; o.textContent = n;
      runSelector.appendChild(o);
    });
  }

  // ฟังก์ชันสำหรับโหลดและเติมข้อมูลลงใน Worker Dropdown
  async function fetchAndPopulateWorkers(runName) {
    // ถ้าเคยโหลดข้อมูลของ Run นี้มาแล้ว ให้ใช้ข้อมูลจาก Cache
    if (workersByRun[runName]) return populateWorkersSelector(workersByRun[runName]);
    workerSelector.innerHTML = '<option>Loading workers...</option>'; // แสดงข้อความ "กำลังโหลด"
    try {
      // โหลดรายชื่อ Workers จาก Server โดยระบุ Run ที่เลือก
      const res = await fetch(`./workers?run=${encodeURIComponent(runName)}`);
      const ws = await res.json();
      workersByRun[runName] = ws; // เก็บข้อมูลลง Cache
      populateWorkersSelector(ws); // นำข้อมูลไปเติมใน Dropdown
    } catch (err) {
      workerSelector.innerHTML = '<option>Failed to load</option>'; // กรณีโหลดไม่สำเร็จ
    }
  }

  // ฟังก์ชันสำหรับเติมข้อมูลลงใน Worker Dropdown
  function populateWorkersSelector(names) {
    workerSelector.innerHTML = '';
    if (names && names.length) {
      names.forEach(n => {
        const o = document.createElement('option');
        o.value = n; o.textContent = n;
        workerSelector.appendChild(o);
      });
    } else {
      workerSelector.innerHTML = '<option>No workers found</option>';
    }
  }

  // ฟังก์ชันสำหรับโหลดข้อมูล Runtime/DAG (เรียกว่า OpTree)
  async function fetchOpTreeData(runName, workerName) {
    const key = `${runName}__${workerName}`; // สร้าง Key สำหรับ Cache
    if (opTreeDataCache[key]) return opTreeDataCache[key]; // ถ้ามีใน Cache ให้ใช้ข้อมูลเก่า
    try {
      // โหลดข้อมูลจาก Server
      const res = await fetch(`./runtime?run=${encodeURIComponent(runName)}&worker=${encodeURIComponent(workerName)}`);
      const d = await res.json();
      opTreeDataCache[key] = d; // เก็บข้อมูลลง Cache
      return d;
    } catch (err) {
      console.error(err);
      return { error: err.message };
    }
  }

  // --- Event Handler Functions ---

  // ฟังก์ชันที่ทำงานเมื่อผู้ใช้เลือก Run ใหม่
  async function handleRunChange() {
    currentState.run = runSelector.value; // อัปเดตสถานะ
    currentState.worker = null;          // รีเซ็ต Worker ที่เลือก
    opTreeDataCache = {};                // ล้าง Cache ข้อมูล OpTree
    commTimingCache = null;              // ล้าง Cache ข้อมูล Communication
    await fetchAndPopulateWorkers(currentState.run); // โหลด Worker ของ Run ใหม่
    if (workerSelector.options.length) {
      workerSelector.selectedIndex = 0; // เลือก Worker แรกเป็นค่าเริ่มต้น
      await handleWorkerChange();       // แล้วทำการโหลดข้อมูลของ Worker นั้น
    } else {
      renderContent(); // ถ้าไม่มี Worker ให้แสดงผลตามสถานะปัจจุบัน
    }
  }

  // ฟังก์ชันที่ทำงานเมื่อผู้ใช้เลือก Worker ใหม่
  async function handleWorkerChange() {
    currentState.worker = workerSelector.value; // อัปเดตสถานะ
    await renderContent(); // แสดงผลข้อมูลของ Worker ที่เลือกใหม่
  }

  // ฟังก์ชันที่ทำงานเมื่อผู้ใช้เลือก View ใหม่
  function handleViewChange() {
    currentState.view = viewSelector.value; // อัปเดตสถานะ
    // ตรวจสอบถ้า View ที่เลือกคือ "table" (Communication Timing)
    if (currentState.view === 'table') {
      // ให้ซ่อน Dropdown ของ Run และ Worker เนื่องจากตารางนี้แสดงข้อมูลสรุปรวม
      workerSelectorGroup.style.display = 'none';
      runSelector.style.display = 'none';
      document.querySelector('label[for="run-selector"]').style.display = 'none';
    } else {
      // ถ้าเป็น View อื่น ให้แสดง Dropdown ตามปกติ
      workerSelectorGroup.style.display = 'block';
      runSelector.style.display = 'block';
      document.querySelector('label[for="run-selector"]').style.display = 'block';
    }
    renderContent(); // แสดงผล View ใหม่
  }

  // --- Rendering Functions ---

  // ฟังก์ชันหลักในการเลือกและแสดงผลข้อมูลตาม View ที่กำหนด
  async function renderContent() {
    // ถ้ายังไม่ได้เลือก Run หรือ Worker (สำหรับ View ที่ต้องใช้) ให้แสดงข้อความบอก
    if (currentState.view !== 'table' && (!currentState.run || !currentState.worker)) {
      contentDisplay.innerHTML = '<p>Please make a selection.</p>';
      return;
    }
    contentDisplay.innerHTML = '<p>Loading...</p>'; // แสดงข้อความ "กำลังโหลด"

    // เลือกฟังก์ชันวาดผลลัพธ์ตาม View ที่เลือก
    if (currentState.view === 'dag') {
      contentTitle.textContent = `DAG for ${currentState.worker}`;
      await renderDagView();
    } else if (currentState.view === 'runtime') {
      contentTitle.textContent = `Runtime for ${currentState.worker}`;
      await renderRuntimeView();
    } else { // view === 'table'
      contentTitle.textContent = `Communication Timing Comparison`;
      await renderCommunicationTable();
    }
  }

  // (ฟังก์ชัน renderSingleWorkerView ไม่ถูกใช้งานจริง)

  // ===== ฟังก์ชันวาดกราฟ DAG =====
  async function renderDagView() {
    // ... (โค้ดส่วนนี้จะทำการโหลดข้อมูลจาก /dag และใช้ D3.js วาดกราฟความสัมพันธ์ของ Operation)
    // ... (มีการคำนวณตำแหน่งโหนดแบบพิเศษ แบ่งเป็นเลนบน-ล่าง และขนาดโหนดแปรผันตามระยะเวลา)
    // ... (มีฟังก์ชัน Zoom/Pan และปุ่ม Reset Zoom)
  }

  // ===== ฟังก์ชันวาดกราฟ Runtime Timeline =====
  async function renderRuntimeView() {
    // ... (โค้ดส่วนนี้จะโหลดข้อมูลจาก fetchOpTreeData และใช้ D3.js วาด Timeline)
    // ... (มีการกำหนดแกน X เป็นเวลา 0-12 หน่วย, แบ่งเลน Computation/Communication)
    // ... (มีฟังก์ชัน Zoom/Pan ที่ซับซ้อน สามารถซูมดูรายละเอียดเวลา และ Label จะแสดงผลตามระดับการซูม)
    // ... (มีการปรับแก้ Scale ของแกน Y เมื่อซูมเข้า เพื่อให้เห็นภาพรวมได้ดีขึ้น)
  }

  // ===== ฟังก์ชันวาดตาราง Communication Timing =====
  async function renderCommunicationTable() {
    // ถ้ายังไม่มีข้อมูลใน Cache ให้ไปโหลดมาก่อน
    if (!commTimingCache) {
      try {
        const response = await fetch('./communication_timing');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        commTimingCache = await response.json();
      } catch (error) {
        contentDisplay.innerHTML = `<p style="color: red;">Failed to fetch...`;
        return;
      }
    }
    // สร้างตาราง HTML จากข้อมูลที่โหลดมา
    contentDisplay.innerHTML = createHtmlTableFromTimingData(commTimingCache);
  }

  // ฟังก์ชันช่วยสร้างตาราง HTML จากข้อมูล Communication Timing
  function createHtmlTableFromTimingData(data) {
    // ... (โค้ดส่วนนี้จะจัดกลุ่ม Runs ที่มี Algorithm เดียวกัน)
    // ... (สร้างตารางเปรียบเทียบ และไฮไลท์ค่าที่น้อยที่สุดในแต่ละคอลัมน์)
    // ... (คืนค่าเป็น String ของ HTML)
  }

  // --- เริ่มต้นการทำงานของแอปพลิเคชัน ---
  initialize();
});
```

---

## 3. สรุปการทำงานของแต่ละโมดูล (View)

หน้าเว็บนี้สามารถแสดงผลข้อมูลได้ 3 รูปแบบ ซึ่งผู้ใช้สามารถเลือกได้จากเมนู "Views"

### 1. DAG Graph (dag)

*   **จุดประสงค์**: แสดงแผนภาพความสัมพันธ์ของ Operation ต่างๆ ในแต่ละ Step ของการเทรนโมเดล ในรูปแบบของ Directed Acyclic Graph (DAG) เพื่อให้เห็นว่า Operation ไหนทำงานก่อน-หลัง และขึ้นอยู่กับอะไร
*   **การแสดงผล**:
    *   แบ่ง Operation ออกเป็น 2 เลน (Lane) คือ **Computation** (การคำนวณ เช่น Forward, Backward) จะอยู่ด้านบน และ **Communication** (การสื่อสารข้อมูลระหว่างโหนด เช่น All-Reduce) จะอยู่ด้านล่าง
    *   การจัดวางโหนดในแนวนอนไม่ได้อิงตามเวลาจริง แต่จะจัดตามลำดับการทำงานและพยายามวางโหนดที่เกี่ยวข้องกัน (เช่น Backward กับ All-Reduce ของมัน) ให้อยู่ใกล้กัน
    *   **ขนาด (ความกว้าง) ของแต่ละโหนดจะแปรผันตามระยะเวลา (duration)** ที่ Operation นั้นใช้ ทำให้สามารถมองเห็น Operation ที่เป็นคอขวด (ใช้เวลานาน) ได้ง่าย
    *   เส้นลูกศรแสดงถึงความสัมพันธ์ (Dependency) ระหว่าง Operation
*   **การโต้ตอบ (Interactivity)**: ผู้ใช้สามารถซูมเข้า-ออก และเลื่อน (Pan) เพื่อสำรวจกราฟในมุมมองต่างๆ ได้

### 2. Communication Timing (table)

*   **จุดประสงค์**: เปรียบเทียบประสิทธิภาพของ Algorithm การสื่อสารข้อมูล (Communication Algorithm) ต่างๆ จากการทดลอง (Runs) หลายๆ ครั้ง
*   **การแสดงผล**:
    *   แสดงผลในรูปแบบของตาราง HTML ที่ชัดเจน
    *   ระบบจะทำการ **จัดกลุ่ม Runs ที่ใช้ชุด Algorithm เดียวกัน** มาไว้ในตารางเดียวกันโดยอัตโนมัติ เพื่อให้เปรียบเทียบได้ง่าย
    *   ในแต่ละคอลัมน์ (แต่ละ Algorithm) จะมีการ **ไฮไลท์ค่าที่น้อยที่สุด (เร็วที่สุด)** ด้วยสีเขียว ทำให้สามารถระบุได้อย่างรวดเร็วว่า Run ไหนใช้วิธีการสื่อสารที่มีประสิทธิภาพสูงสุดสำหรับ Algorithm นั้นๆ
*   **การโต้ตอบ**: เป็นการแสดงผลแบบ Static ไม่มี Interactivity ซับซ้อน

### 3. Runtime (runtime)

*   **จุดประสงค์**: แสดง Timeline การทำงานของ Worker แต่ละตัวในแต่ละ Step เพื่อวิเคราะห์ลำดับและระยะเวลาการทำงานของ Operation ต่างๆ บนแกนเวลาจริง
*   **การแสดงผล**:
    *   เป็นกราฟรูปแบบ Gantt Chart ที่มี 2 เลนแนวนอน คือ **Computation** และ **Communication**
    *   แกน X คือเวลา (เริ่มต้นที่โดเมน 0 ถึง 12 หน่วยเวลา)
    *   แต่ละ Operation จะถูกวาดเป็นแท่งสี่เหลี่ยมบนเลนของตัวเอง ตำแหน่งและความยาวของแท่งสี่เหลี่ยมจะสอดคล้องกับเวลาเริ่มต้นและระยะเวลาที่ใช้จริง
*   **การโต้ตอบ**:
    *   เป็น View ที่มี Interactivity สูงที่สุด ผู้ใช้สามารถ **ซูมและเลื่อน (Zoom/Pan)** บนแกนเวลาได้อย่างละเอียด
    *   เมื่อซูมเข้า **แกนเวลาจะปรับความละเอียดอัตโนมัติ** เพื่อแสดงตัวเลขที่เหมาะสม
    *   **ป้ายชื่อ (Label) ของแต่ละ Operation จะปรากฏขึ้นมา** เมื่อซูมเข้าไปในระดับที่เพียงพอ เพื่อไม่ให้การแสดงผลรกจนเกินไปในมุมมองภาพรวม
    *   เมื่อนำเมาส์ไปชี้ที่แท่ง Operation จะมี **Tooltip** แสดงข้อมูลรายละเอียด (ชื่อเต็ม, เวลาเริ่มต้น, เวลาสิ้นสุด, ระยะเวลา)
    *   มีปุ่ม "Reset zoom" เพื่อกลับไปยังมุมมองเริ่มต้น
