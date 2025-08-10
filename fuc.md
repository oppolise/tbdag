# คำอธิบายการทำงานของฟังก์ชันการแสดงผล (Rendering Functions)

เอกสารนี้อธิบายการทำงานแบบละเอียด (Line-by-line) ของฟังก์ชัน JavaScript หลัก 3 ฟังก์ชันที่ใช้ในการแสดงผลข้อมูลในหน้า CGS-DNN Analysis ได้แก่ `renderDagView()`, `renderRuntimeView()`, และ `renderCommunicationTable()`

---

## 1. `renderDagView()` - ฟังก์ชันวาดกราฟ DAG

ฟังก์ชันนี้มีหน้าที่ในการดึงข้อมูล DAG (Directed Acyclic Graph) จากเซิร์ฟเวอร์และวาดเป็นกราฟที่แสดงความสัมพันธ์ของ Operations โดยขนาดของโหนดจะแปรผันตามระยะเวลาการทำงาน

```javascript
async function renderDagView() {
  try {
    // 1. ดึงข้อมูล DAG จาก API endpoint "/dag"
    // โดยส่งพารามิเตอร์ run และ worker ที่ผู้ใช้เลือกไปกับ URL
    const res = await fetch(`./dag?run=${encodeURIComponent(currentState.run)}&worker=${encodeURIComponent(currentState.worker)}`);
    // 2. ตรวจสอบว่าการร้องขอสำเร็จหรือไม่ (HTTP status 200-299)
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    // 3. แปลงข้อมูลที่ได้รับจาก JSON string เป็น JavaScript object
    const data = await res.json();
    // 4. ดึง key ของแต่ละ step (เช่น "0", "1") แล้วเรียงลำดับจากน้อยไปมาก
    const stepKeys = Object.keys(data).sort((a,b)=> Number(a)-Number(b));
    // 5. ล้างเนื้อหาเก่าในพื้นที่แสดงผลออก
    contentDisplay.innerHTML = '';

    // 6. วนลูปเพื่อวาดกราฟสำหรับแต่ละ Step ที่ได้ข้อมูลมา
    stepKeys.forEach(stepKey => {
      // 7. ดึงข้อมูลของ step ปัจจุบัน
      const step = data[stepKey];
      // 8. ดึงข้อมูลโหนด (nodes) และเส้นเชื่อม (edges) ของ step นี้
      const nodes = step.nodes || []; const edges = step.edges || [];

      // 9. กำหนดค่าพื้นฐานสำหรับ layout ของกราฟ (margin, ขนาด)
      const margin = {top:20,right:24,bottom:20,left:24};
      const width = Math.max(1100, window.innerWidth - 260); // ความกว้าง responsive
      const height = 360; // ความสูงคงที่
      const laneY = { top: 110, bottom: 270 }; // ตำแหน่งแกน Y ของเลนบนและเลนล่าง

      // 10. สร้าง Element สำหรับการ์ด (Card) ที่จะครอบกราฟทั้งหมด
      const card = document.createElement('div'); card.className = 'card';
      const header = document.createElement('div'); header.className='card-header';
      const controls = document.createElement('div'); controls.className='card-controls';
      const legend = document.createElement('div'); legend.className='legend';
      // 11. สร้าง Legend (คำอธิบายสัญลักษณ์) ด้วย HTML string
      legend.innerHTML = `
        <div class="legend-item"><span class="legend-swatch" style="background:#b9defa;border-color:#7fb8e0"></span>Computation</div>
        <div class="legend-item"><span class="legend-swatch" style="background:#f8c9b3;border-color:#e28a2b"></span>Communication</div>
      `;
      // 12. เพิ่ม Legend และปุ่ม "Reset zoom" เข้าไปในส่วนควบคุม (controls)
      controls.appendChild(legend);
      const btnReset = document.createElement('button'); btnReset.textContent = 'Reset zoom'; controls.appendChild(btnReset);
      // 13. ตั้งชื่อหัวข้อของการ์ดเป็น "Step [หมายเลข]"
      header.innerHTML = `<h4 class="card-title">Step ${stepKey}</h4>`;
      header.appendChild(controls);
      const body = document.createElement('div'); body.className='card-body';
      // 14. ใช้ D3.js สร้าง SVG element ภายใน body ของการ์ด
      const svg = d3.select(body).append('svg').attr('width', width).attr('height', height).attr('viewBox', `0 0 ${width} ${height}`);
      // 15. ประกอบร่างการ์ดทั้งหมดแล้วนำไปแสดงในพื้นที่ contentDisplay
      card.appendChild(header); card.appendChild(body);
      contentDisplay.appendChild(card);

      // 16. สร้างพื้นที่สี่เหลี่ยมโปร่งใสสำหรับรับ Event การซูม
      svg.append('rect').attr('x',0).attr('y',0).attr('width',width).attr('height',height).style('fill','transparent');
      // 17. สร้าง Group (<g>) หลักสำหรับวัตถุทั้งหมดในกราฟ เพื่อให้ซูมและเลื่อนพร้อมกันได้
      const gMain = svg.append('g').attr('class','dag-inner');

      // 18. สร้าง Scale สำหรับแปลงค่า duration (เวลาที่ใช้) ของโหนดไปเป็นความกว้าง (pixel)
      const maxDur = d3.max(nodes.map(n => n.dur || 0)) || 1; // หา duration สูงสุด
      const durScale = d3.scaleLinear().domain([0, maxDur]).range([70, 260]); // map duration 0..max ไปเป็นความกว้าง 70..260px
      const nodeHeight = 34, radius = 8;
      const nodeWidth = n => durScale(n.dur || 0); // ฟังก์ชันสำหรับหาความกว้างของโหนด
      const nodesById = new Map(nodes.map(n => [n.id, n])); // สร้าง Map เพื่อให้ค้นหาโหนดด้วย ID ได้เร็วขึ้น

      // --- ส่วนของการคำนวณ Layout ---
      // 19. แยกโหนดสำหรับเลนบน (Computation) และเลนล่าง (Communication)
      const topNodes = nodes.filter(n => n.lane === 'top');
      const bottomNodes = nodes.filter(n => n.lane === 'bottom');
      const gap = 46; // ระยะห่างระหว่างโหนด

      // 20. ฟังก์ชันสำหรับจัดวางโหนดในแนวนอนแบบง่ายๆ โดยเรียงต่อกันไปเรื่อยๆ
      function layoutLinearX(list, startX) {
        const positions = new Map();
        let x = startX;
        list.forEach(n => {
          const w = nodeWidth(n);
          const cx = x + w/2; // คำนวณจุดศูนย์กลาง
          positions.set(n.id, cx);
          x += w + gap; // เลื่อนตำแหน่งสำหรับโหนดถัดไป
        });
        return positions;
      }
      // 21. คำนวณตำแหน่งของโหนดในเลนบน
      const topPositions = layoutLinearX(topNodes, margin.left + 70);

      // 22. จัดตำแหน่งโหนดเลนล่าง (Communication) โดยอิงกับโหนดในเลนบนที่เกี่ยวข้อง
      const commAnchors = [];
      edges.forEach(e => {
        // หาเส้นเชื่อมที่บอกความสัมพันธ์ระหว่างเลนบนและเลนล่าง
        if (e.kind === 'bcast_to_first_forward') {
          commAnchors.push({comm: e.source, anchor: e.target, type: 'broadcast'});
        } else if (e.kind === 'backward_to_allreduce') {
          commAnchors.push({comm: e.target, anchor: e.source, type: 'allreduce'});
        }
      });

      // 23. คำนวณตำแหน่ง X ที่ "ต้องการ" ของโหนด communication โดยอิงจาก anchor
      const desired = new Map();
      commAnchors.forEach(({comm, anchor, type}) => {
        const ax = topPositions.get(anchor) || (margin.left + 70);
        let targetX = ax;
        if (type === 'broadcast') {
          // พยายามวาง broadcast ไว้ทางซ้ายของ forward op แรก
          const n = nodesById.get(comm); const w = nodeWidth(n);
          targetX = (ax - (nodeWidth(nodesById.get(anchor)) / 2)) - 30 - w/2;
        }
        desired.set(comm, targetX);
      });

      // 24. จัดเรียงโหนดเลนล่างตามตำแหน่ง X ที่ต้องการ แล้วเลื่อนเพื่อไม่ให้โหนดซ้อนทับกัน
      const bottomOrder = bottomNodes.map(n => ({n, ax: desired.get(n.id) ?? (margin.left + 70)}))
                                     .sort((a,b)=> a.ax - b.ax);
      const bottomPositions = new Map();
      let lastRight = margin.left + 40;
      bottomOrder.forEach(({n, ax}) => {
        const w = nodeWidth(n);
        let cx = Math.max(ax, lastRight + gap + w/2); // คำนวณตำแหน่ง X ใหม่ป้องกันการทับกัน
        bottomPositions.set(n.id, cx);
        lastRight = cx + w/2;
      });

      // 25. สร้าง Map สุดท้ายที่เก็บตำแหน่ง (x, y) ของทุกโหนด
      const nodeIdxMap = new Map();
      nodes.forEach(n => {
        if (n.lane==='top') { nodeIdxMap.set(n.id, {x: topPositions.get(n.id), y: laneY.top}); }
        else { nodeIdxMap.set(n.id, {x: bottomPositions.get(n.id), y: laneY.bottom}); }
      });

      // --- ส่วนของการวาด ---
      // 26. วาดเส้นเชื่อม (Edges)
      const defs = svg.append('defs');
      // 27. สร้าง "marker" สำหรับหัวลูกศรที่จะนำไปใช้กับเส้นเชื่อม
      defs.append('marker').attr('id','arrow').attr('viewBox','0 0 10 10').attr('refX',8).attr('refY',5).attr('markerWidth',7).attr('markerHeight',7).attr('orient','auto')
        .append('path').attr('d','M 0 0 L 10 5 L 0 10 z').attr('fill','#999');

      // 28. วนลูปเพื่อวาดเส้นเชื่อมแต่ละเส้น
      edges.forEach(e => {
        const s = nodeIdxMap.get(e.source); // ตำแหน่งต้นทาง
        const t = nodeIdxMap.get(e.target); // ตำแหน่งปลายทาง
        if (!s || !t) return;
        // 29. วาดเส้นโค้งแบบ Cubic Bezier ระหว่างสองจุด
        gMain.append('path')
          .attr('d', `M${s.x},${s.y} C ${s.x},${(s.y+t.y)/2} ${t.x},${(s.y+t.y)/2} ${t.x},${t.y}`)
          .attr('fill','none').attr('stroke','#b4b4b4').attr('stroke-width',1.6)
          .attr('marker-end','url(#arrow)'); // เพิ่มหัวลูกศรที่ปลายเส้น
      });

      // 30. วาดโหนด (Nodes)
      const g = gMain.append('g');
      const nodeText = [];
      nodes.forEach(n => {
        const pos = nodeIdxMap.get(n.id); if (!pos) return;
        const w = nodeWidth(n);
        const x = pos.x - w/2; const y = pos.y - nodeHeight/2;
        // 31. กำหนด class ตามประเภทของโหนด (computation หรือ communication) เพื่อกำหนดสี
        const cls = n.category === 'computation' ? 'cm-box' : 'cp-box';
        // 32. วาดสี่เหลี่ยมมุมมนสำหรับแต่ละโหนด
        g.append('rect').attr('x',x).attr('y',y).attr('width',w).attr('height',nodeHeight).attr('rx',radius).attr('ry',radius).attr('class',cls);
        nodeText.push({id:n.id, x:pos.x, y:pos.y, data:n});
      });
      // 33. วาดข้อความ (Label) บนโหนด
      const texts = g.selectAll('text')
        .data(nodeText, d=>d.id)
        .enter()
        .append('text')
        .attr('x', d=>d.x).attr('y', d=>d.y+4) // จัดตำแหน่งให้อยู่กึ่งกลาง
        .attr('text-anchor','middle').attr('class','block-label')
        .text(d => d.data.label || d.id);

      // --- ส่วนของ Interactivity (Zoom/Pan) ---
      // 34. ฟังก์ชันสำหรับซ่อน/แสดง Label ตามระดับการซูม
      function updateLabelVisibility(k){
        texts.each(function(d){
          const show = nodeWidth(d.data) * k >= 48; // แสดง label ถ้าความกว้างของโหนดหลังซูมแล้วมีขนาดใหญ่พอ
          d3.select(this).style('display', show ? null : 'none');
        });
      }

      // 35. สร้าง d3.zoom instance
      const zoom = d3.zoom()
        .scaleExtent([0.5, 8]) // กำหนดช่วงการซูม (0.5x ถึง 8x)
        .translateExtent([[-10000, -10000], [width + 10000, height + 10000]]) // กำหนดขอบเขตการเลื่อน
        .on('zoom', (event) => { // Event ที่จะทำงานเมื่อมีการซูม/เลื่อน
          gMain.attr('transform', event.transform); // ย้ายตำแหน่งและขนาดของ group หลัก
          updateLabelVisibility(event.transform.k); // อัปเดตการแสดงผลของ label
        });
      // 36. ผูก zoom handler เข้ากับ SVG
      svg.call(zoom);

      // 37. คำนวณการซูมเริ่มต้นเพื่อให้กราฟทั้งหมดพอดีกับการ์ด (Fit to card)
      const paddingLeft = 40, paddingRight = 40, paddingTop = 20, paddingBottom = 20;
      const bbox = gMain.node().getBBox(); // หาขนาดจริงของ content
      const contentW = Math.max(1, bbox.width);
      const contentH = Math.max(1, bbox.height);
      const scaleX = (width - paddingLeft - paddingRight) / contentW;
      const scaleY = (height - paddingTop - paddingBottom) / contentH;
      const fitScale = Math.min(1, Math.min(scaleX, scaleY)); // หา scale ที่เหมาะสมที่สุด
      const tx = (-bbox.x * fitScale) + paddingLeft;
      const ty = (-bbox.y * fitScale) + paddingTop;
      const initialT = d3.zoomIdentity.translate(tx, ty).scale(fitScale); // สร้าง transform เริ่มต้น
      // 38. ตั้งค่าการซูมเริ่มต้น
      svg.call(zoom.transform, initialT);
      updateLabelVisibility(fitScale);

      // 39. ตั้งค่า event listener ให้กับปุ่ม Reset Zoom
      btnReset.addEventListener('click', () => {
        svg.transition().duration(300).call(zoom.transform, initialT); // ค่อยๆ กลับไปที่การซูมเริ่มต้น
      });
    });
  } catch (err) {
    // 40. หากเกิดข้อผิดพลาด ให้แสดงข้อความ error
    contentDisplay.innerHTML = `<p style="color:red">Failed to render DAG: ${err.message}</p>`;
  }
}
```

---

## 2. `renderRuntimeView()` - ฟังก์ชันวาดกราฟ Timeline

ฟังก์ชันนี้มีหน้าที่วาด Timeline (Gantt chart) ของ Operations เพื่อแสดงว่าแต่ละอย่างทำงานเมื่อไหร่และนานเท่าไหร่บนแกนเวลาจริง

```javascript
async function renderRuntimeView() {
  // 1. ดึงข้อมูล Runtime จาก `fetchOpTreeData` ซึ่งมีระบบ cache ในตัว
  const data = await fetchOpTreeData(currentState.run, currentState.worker);
  // 2. หากข้อมูลผิดพลาดหรือไม่มี ให้แสดงข้อความแจ้งเตือนและจบการทำงาน
  if (!data || data.error) { contentDisplay.innerHTML = `<p style="color:red">Failed to load data. ${data ? data.error : ''}</p>`; return; }

  // 3. ดึง key ของแต่ละ step และเรียงลำดับ
  const stepKeys = Object.keys(data).sort((a,b)=> Number(a)-Number(b));
  if (!stepKeys.length) { contentDisplay.innerHTML = '<p>No runtime data.</p>'; return; }

  // 4. ล้างพื้นที่แสดงผลและสร้าง container หลัก
  contentDisplay.innerHTML = '';
  const outerContainer = document.createElement('div');
  outerContainer.style.width = '100%';
  outerContainer.style.overflowX = 'auto'; // ทำให้เลื่อนแนวนอนได้ถ้ากราฟยาว
  contentDisplay.appendChild(outerContainer);

  // 5. กำหนดโดเมนของเวลาเริ่มต้น (0 ถึง 12 หน่วยเวลา)
  const domainMin = 0, domainMax = 12;
  const domainSpan = domainMax - domainMin;

  // 6. วนลูปเพื่อวาด Timeline ของแต่ละ Step
  stepKeys.forEach(stepKey => {
    const step = data[stepKey];

    // 7. แปลงข้อมูลจาก API (ที่มีโครงสร้างซับซ้อน) ให้เป็น Array ของ block ที่จะวาด
    const compBlocks = [], commBlocks = []; // แยกเก็บ Computation และ Communication
    const pushComp = (name,s,e) => { const st=Number(s||0), ed=Number(e||s||0); compBlocks.push({name:name||'', start:st, end:ed, dur:ed-st}); };
    // 8. ดึงข้อมูลจากส่วนต่างๆ (forward, loss, backward, optimizer) มาใส่ใน `compBlocks`
    if (step.forward) (Array.isArray(step.forward)?step.forward:[step.forward]).forEach(it=>pushComp(it.name,it.start_time,it.end_time));
    if (step.loss)    (Array.isArray(step.loss)?step.loss:[step.loss]).forEach(it=>pushComp(it.name,it.start_time,it.end_time));
    if (step.backward) (Array.isArray(step.backward)?step.backward:[step.backward]).forEach(it=>pushComp(it.name,it.start_time,it.end_time));
    if (step.optimizer) (Array.isArray(step.optimizer)?step.optimizer:[step.optimizer]).forEach(it=>pushComp(it.name,it.start_time,it.end_time));
    // 9. ดึงข้อมูล broadcasts และ all_reduce มาใส่ใน `commBlocks`
    (step.broadcasts||[]).forEach(ev=>{ const s=Number(ev.start_time||0), e=Number(ev.end_time||ev.start_time||0); commBlocks.push({name:'broadcast',start:s,end:e,dur:e-s}); });
    // 10. ฟังก์ชันที่วนซ้ำในตัวเอง (recursive) เพื่อหา all_reduce ที่ซ่อนอยู่ใน backward
    (function collectAllReduce(events){ if(!Array.isArray(events))return; events.forEach(ev=>{ (ev.children||[]).forEach(ch=>{ if (ch.name==='nccl:all_reduce'||(ch.name&&ch.name.toLowerCase().includes('all_reduce'))) { commBlocks.push({name:ch.name||'all_reduce',start:Number(ch.start_time||0),end:Number(ch.end_time||ch.start_time||0),dur:Number(ch.dur||(ch.end_time-ch.start_time)||0)}); } }); collectAllReduce(ev.children||[]); }); })(step.backward||[]);

    // 11. กำหนดค่า layout และขนาดของ SVG
    const margin = {top:20, right:24, bottom:70, left:160};
    const laneBase = 80;
    const laneHeight = Math.round(laneBase * 1.3); // ความสูงของเลน
    const gap = 24;
    const wrapperWidth = Math.max(700, window.innerWidth - 360);
    const innerWidth = Math.max(600, wrapperWidth - margin.left - margin.right);
    const width = innerWidth + margin.left + margin.right;
    const height = margin.top + (laneHeight+gap)*2 - gap + margin.bottom;

    // 12. สร้างโครงสร้าง Card, Header, Controls, Legend, Body, และ SVG เหมือนกับ `renderDagView`
    const card = document.createElement('div'); card.className = 'card';
    const header = document.createElement('div'); header.className='card-header';
    header.innerHTML = `<h4 class="card-title">Step ${stepKey}</h4>`;
    const controls = document.createElement('div'); controls.className='card-controls';
    const legend = document.createElement('div'); legend.className = 'legend';
    legend.innerHTML = `...`;
    controls.appendChild(legend);
    const btnReset = document.createElement('button'); btnReset.textContent='Reset zoom';
    controls.appendChild(btnReset);
    header.appendChild(controls);
    const body = document.createElement('div'); body.className='card-body';
    const svgWrap = document.createElement('div'); svgWrap.className='runtime-wrapper'; svgWrap.style.width = width + 'px';
    body.appendChild(svgWrap);
    card.appendChild(header); card.appendChild(body);
    outerContainer.appendChild(card);

    // 13. สร้าง SVG element ด้วย D3
    const svg = d3.select(svgWrap).append('svg').attr('class','runtime-svg')...;

    // 14. สร้าง Scale สำหรับแกน X เพื่อแปลงค่าเวลา (domain) ไปเป็นตำแหน่งบนหน้าจอ (range)
    const x = d3.scaleLinear().domain([domainMin, domainMax]).range([margin.left, margin.left + innerWidth]);

    // 15. คำนวณตำแหน่งแกน Y ของแต่ละเลน
    const lanes = ['Computation','Communication'];
    const laneY = {};
    lanes.forEach((l,i) => { laneY[l] = margin.top + i*(laneHeight+gap); });

    // 16. วาดแกน X ด้านล่าง
    const axisG = svg.append('g').attr('class','x-axis').attr('transform', `translate(0, ${margin.top + (laneHeight+gap)*lanes.length + 10})`);
    axisG.call(d3.axisBottom(x).ticks(domainSpan + 1).tickFormat(d3.format('d'))).selectAll('text').attr('class','tick-text');

    // 17. สร้าง Clip Path เพื่อป้องกันไม่ให้กราฟวาดทับแกน X หรือส่วนอื่นๆ
    const clipId = `clip-${stepKey}`.replace(/[^\w-]/g,'');
    svg.append('defs').append('clipPath').attr('id', clipId).append('rect')...;

    // 18. สร้าง Group (<g>) หลักสำหรับแท่งกราฟทั้งหมด และผูกกับ Clip Path
    const inner = svg.append('g').attr('class','inner-group').attr('clip-path', `url(#${clipId})`);

    // 19. รวมข้อมูล block ทั้งสองประเภทเข้าด้วยกันเพื่อเตรียมวาด
    const blocks = [];
    compBlocks.forEach(b => blocks.push(Object.assign({}, b, { lane:'Computation', y: laneY['Computation'] + 10 })));
    commBlocks.forEach(b => blocks.push(Object.assign({}, b, { lane:'Communication', y: laneY['Communication'] + 10 })));

    // 20. ใช้ D3.js data binding เพื่อวาดสี่เหลี่ยม (rect) สำหรับแต่ละ block
    inner.selectAll('.block-rect')
      .data(blocks, d => d.name + '_' + d.start + '_' + d.end) // key ช่วยให้ D3 อัปเดตข้อมูลได้ถูกต้อง
      .enter()
      .append('rect')
      .attr('class', d => 'block-rect ' + (d.lane === 'Computation' ? 'cm-box' : 'cp-box'))
      .attr('x', d => Math.max(margin.left, x(d.start))) // คำนวณตำแหน่ง X จากเวลาเริ่มต้น
      .attr('y', d => d.y)
      .attr('width', d => Math.max(2, x(d.end) - x(d.start))) // คำนวณความกว้างจาก duration
      .attr('height', laneHeight - 20)
      .attr('rx', 6).attr('ry', 6)
      // 21. เพิ่ม Event Listener สำหรับแสดง Tooltip เมื่อผู้ใช้ชี้เมาส์
      .on('mouseover', function(event,d){ ... })
      .on('mousemove', function(event){ ... })
      .on('mouseout', function(){ ... });

    // 22. สร้าง Group สำหรับ Label ที่จะวางซ้อนบนกราฟ (เพื่อไม่ให้ Label ถูกยืด/หดตามการซูม)
    const labelsOverlay = svg.append('g').attr('class','labels-overlay');
    const labelNodes = labelsOverlay.selectAll('.block-label')
      .data(blocks, d => d.name + '_' + d.start + '_' + d.end)
      .enter().append('text')...;

    // 23. สร้าง d3.zoom instance สำหรับจัดการการซูมและเลื่อน
    const zoom = d3.zoom()
      .scaleExtent([1, 800]) // กำหนดให้ซูมเข้าได้อย่างเดียว (สูงสุด 800x)
      .translateExtent(...)
      .on('zoom', (event) => { // Event ที่ทำงานเมื่อซูม
        let t = event.transform; // transform ปัจจุบัน (scale, translate x, translate y)
        const k = t.k; // ค่า scale

        // 24. จำกัดขอบเขตการเลื่อน (Pan) ในแกน X เพื่อไม่ให้กราฟหลุดออกจากขอบเขต 0-12 ตอนที่ยังไม่ซูม
        let clampedTx = Math.max(minTx, Math.min(maxTx, t.x));

        // 25. คำนวณการย่อแกน Y เมื่อซูมเข้า เพื่อให้เห็นภาพรวมของเลนได้ดีขึ้น
        let yScale = 1 / (1 + 0.6 * Math.max(0, k - 1));
        yScale = Math.max(0.25, Math.min(1, yScale));

        // 26. จำกัดขอบเขตการเลื่อนในแกน Y เพื่อไม่ให้กราฟชนขอบบนหรือแกน X ด้านล่าง
        let clampedTy = Math.max(minTy, Math.min(t.y, maxTy));

        // 27. ใช้ transform ที่คำนวณแล้วกับ Group หลักของกราฟ
        inner.attr('transform', `translate(${clampedTx}, ${clampedTy}) scale(${k}, ${yScale})`);

        // 28. อัปเดตแกน X ตามระดับการซูมใหม่
        const newX = d3.zoomIdentity.translate(clampedTx, 0).scale(k).rescaleX(x);
        // 29. คำนวณจำนวนขีด (ticks) และทศนิยมบนแกน X ให้เหมาะสมกับระดับการซูม
        const tickCount = ...;
        const tickVals = newX.ticks(tickCount);
        const fmt = d3.format(`.${decimals}f`);
        // 30. วาดแกน X ใหม่
        axisG.call(d3.axisBottom(newX).tickValues(tickVals).tickFormat(d => fmt(d)))...;
        axisG.raise(); // ย้ายแกนมาไว้บนสุดเสมอ

        // 31. คำนวณตำแหน่งของ Label ใหม่หลังการซูม (เพื่อให้ Label มีขนาดคงที่)
        const labelPositions = [];
        labelNodes.each(function(d) { ... });
        // 32. จัดการการซ่อน/แสดง Label เพื่อไม่ให้แสดงผลทับกัน
        labelPositions.sort((a,b)=> a.x - b.x);
        let lastX = -Infinity;
        labelPositions.forEach(p => {
          if (p.w < 40 || (p.x - lastX) < 48) node.classed('hidden-label', true);
          else { node.classed('hidden-label', false).attr('x', p.x).attr('y', p.y); lastX = p.x; }
        });
      });

    // 33. ผูก zoom handler เข้ากับ SVG
    svg.call(zoom);

    // 34. เรียกฟังก์ชันอัปเดตครั้งแรกเพื่อจัดวาง Label ให้ถูกต้องตั้งแต่เริ่ม
    (function initialUpdate() { ... })();

    // 35. ตั้งค่าปุ่ม Reset Zoom
    btnReset.addEventListener('click', () => svg.transition().duration(300).call(zoom.transform, d3.zoomIdentity));

    // ... ฟังก์ชันช่วยอื่นๆ
  });
}
```

---

## 3. `renderCommunicationTable()` - ฟังก์ชันวาดตารางเปรียบเทียบ

ฟังก์ชันนี้มีหน้าที่ดึงข้อมูลสรุปของ Communication Timing และสร้างเป็นตาราง HTML เพื่อเปรียบเทียบประสิทธิภาพ

```javascript
async function renderCommunicationTable() {
  // 1. ตรวจสอบว่าเคยโหลดข้อมูลนี้มาแล้วหรือยัง (ดูใน cache)
  if (!commTimingCache) {
    try {
      // 2. ถ้ายังไม่เคยโหลด ให้ดึงข้อมูลจาก endpoint "/communication_timing"
      const response = await fetch('./communication_timing');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      // 3. แปลงข้อมูล JSON และเก็บไว้ใน cache
      commTimingCache = await response.json();
    } catch (error) {
      // 4. หากเกิดข้อผิดพลาด ให้แสดงข้อความ error
      contentDisplay.innerHTML = `<p style="color: red;">Failed to fetch communication timing data: ${error.message}</p>`; return;
    }
  }
  // 5. เรียกใช้ฟังก์ชัน `createHtmlTableFromTimingData` เพื่อสร้าง HTML string จากข้อมูล
  contentDisplay.innerHTML = createHtmlTableFromTimingData(commTimingCache);
}

// ฟังก์ชันนี้เป็นฟังก์ชันช่วย (Helper function) ที่ถูกเรียกโดย `renderCommunicationTable`
function createHtmlTableFromTimingData(data) {
  // 1. ตรวจสอบว่ามีข้อมูลหรือไม่
  if (!data || Object.keys(data).length === 0) return '<h3>...</h3><p>No ... data available.</p>';

  // 2. สร้าง object `runGroups` เพื่อจัดกลุ่ม "Run" ที่มีชุด Algorithm การสื่อสารเหมือนกัน
  const runGroups = {};
  for (const runName in data) {
    const keys = Object.keys(data[runName]); // ดึงชื่อ Algorithm ทั้งหมดของ Run นั้น
    const signature = keys.join(','); // สร้าง "ลายเซ็น" จากชื่อ Algorithm
    if (!runGroups[signature]) runGroups[signature] = [];
    runGroups[signature].push(runName); // เพิ่ม Run เข้าไปในกลุ่มที่มีลายเซ็นเดียวกัน
  }

  // 3. วนลูปตามกลุ่มที่สร้างขึ้นเพื่อสร้างตารางสำหรับแต่ละกลุ่ม
  let finalHtml = ''; let tableCount = 0;
  for (const signature in runGroups) {
    if (!signature) continue;
    tableCount++;
    const runsInGroup = runGroups[signature];
    const firstRunInGroup = runsInGroup[0];
    const headers = Object.keys(data[firstRunInGroup]); // ดึงหัวตาราง (ชื่อ Algorithm)

    // 4. คำนวณหาค่าที่น้อยที่สุด (min value) ในแต่ละคอลัมน์เพื่อใช้ไฮไลท์
    const minValues = {};
    if (runsInGroup.length > 1) { // การเปรียบเทียบจะทำเมื่อมีมากกว่า 1 Run ในกลุ่ม
      headers.forEach(header => {
        const values = runsInGroup.map(r => data[r][header]).filter(v => v !== undefined);
        if (values.length) minValues[header] = Math.min(...values);
      });
    }

    // 5. สร้าง HTML string สำหรับตาราง
    finalHtml += `<div ...><h3>Communication Timing Table #${tableCount}</h3>`;
    let table = '<table class="timing-table">';
    // 6. สร้างส่วนหัวของตาราง (thead)
    table += '<thead><tr><th>Communication Algorithm</th>';
    headers.forEach(h => table += `<th>${h}</th>`);
    table += '</tr></thead><tbody>';
    // 7. สร้างส่วนเนื้อหาของตาราง (tbody) โดยวนลูปตาม Run ในกลุ่ม
    runsInGroup.forEach(runName => {
      table += `<tr><td>${runName}</td>`;
      const row = data[runName];
      headers.forEach(h => {
        const v = row[h];
        // 8. ตรวจสอบว่าค่านี้เป็นค่าที่น้อยที่สุดหรือไม่ ถ้าใช่ ให้เพิ่ม class "highlight-min"
        const highlightClass = (v !== undefined && v === minValues[h]) ? 'highlight-min' : '';
        table += `<td class="${highlightClass}">${v !== undefined ? v.toFixed(3) : 'N/A'}</td>`;
      });
      table += '</tr>';
    });
    table += '</tbody></table></div>';
    finalHtml += table;
  }
  // 9. คืนค่า HTML string ทั้งหมดที่สร้างเสร็จแล้ว
  return finalHtml;
}
```
