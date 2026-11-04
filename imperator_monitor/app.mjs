const finite = value => typeof value === 'number' && Number.isFinite(value);

export function netPnl(trade, price) {
  const {entry_price: entry, margin, leverage, fee_rate: fee} = trade;
  if (!finite(price) || price <= 0 || !finite(entry) || entry <= 0 ||
      !finite(margin) || margin <= 0 || !finite(leverage) || leverage <= 0 ||
      !finite(fee) || fee < 0 || fee >= 1 || !['BUY', 'SELL'].includes(trade.side)) return null;
  const ratio = price / entry;
  const grossPercent = (trade.side === 'BUY' ? 1 : -1) * (ratio - 1) * leverage * 100;
  const fees = margin * leverage * fee * (1 + ratio);
  const netUsdt = margin * grossPercent / 100 - fees;
  const netPercent = netUsdt / margin * 100;
  return [grossPercent, fees, netUsdt, netPercent].every(finite)
    ? {grossPercent, fees, netUsdt, netPercent} : null;
}

export const candleTime = seconds => finite(seconds) ? Math.floor(seconds / 3600) * 3600 : null;

export function utcText(seconds) {
  return finite(seconds) ? new Date(seconds * 1000).toISOString().replace('T', ' ').slice(0, 19) : '—';
}

function number(value, digits = 2, sign = false) {
  if (!finite(value)) return '—';
  return (sign && value > 0 ? '+' : '') + value.toLocaleString('ru-RU', {minimumFractionDigits: digits, maximumFractionDigits: digits});
}

function priceText(value) {
  if (!finite(value)) return '—';
  return number(value, value < 1 ? 6 : value < 10 ? 4 : 2);
}

function start() {
  const $ = id => document.getElementById(id);
  const L = window.LightweightCharts;
  if (!L?.createChart || !L?.createSeriesMarkers) {
    $('messages').classList.add('visible');
    $('messages').textContent = 'Не найден модуль графика. Распакуй архив панели целиком, включая папку vendor.';
    return;
  }

  let state = null, journalError = '', marketError = '', historyError = '';
  let symbol = 'BTCUSDT', selectedId = null, mode = 'live', historyBusy = false;
  let stateBusy = false, marketBusy = false, marketController = null, historySequence = 0;
  let liveMarkets = new Map(), chartCandles = [], times = new Set(), chartKey = '';
  let tableSignature = '', positionsSignature = '', markerSignature = '', levelSignature = '';
  let priceLines = [];

  const chart = L.createChart($('chart'), {
    layout: {background: {type: 'solid', color: '#101827'}, textColor: '#8d9bb2', fontFamily: 'Segoe UI, system-ui, sans-serif'},
    grid: {vertLines: {color: '#1b2639'}, horzLines: {color: '#1b2639'}},
    rightPriceScale: {borderColor: '#26354d'},
    timeScale: {borderColor: '#26354d', timeVisible: true, secondsVisible: false, rightOffset: 7},
    crosshair: {mode: L.CrosshairMode.Normal},
    localization: {locale: 'ru-RU', timeFormatter: time => utcText(Number(time)).slice(0, 16) + ' UTC'},
  });
  const series = chart.addSeries(L.CandlestickSeries, {
    upColor: '#26b99a', downColor: '#ec5c77', wickUpColor: '#26b99a', wickDownColor: '#ec5c77', borderVisible: false,
    priceFormat: {type: 'price', precision: 2, minMove: 0.01},
  });
  series.priceScale().applyOptions({scaleMargins: {top: 0.13, bottom: 0.24}});
  const volume = chart.addSeries(L.HistogramSeries, {priceFormat: {type: 'volume'}, priceScaleId: 'volume', lastValueVisible: false, priceLineVisible: false});
  chart.priceScale('volume').applyOptions({scaleMargins: {top: 0.83, bottom: 0}, visible: false});
  const connector = chart.addSeries(L.LineSeries, {color: '#78a8ff', lineWidth: 1, lineStyle: L.LineStyle.Dashed, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false});
  const markers = L.createSeriesMarkers(series, []);
  const resize = () => chart.applyOptions({width: $('chart').clientWidth, height: $('chart').clientHeight});
  new ResizeObserver(resize).observe($('chart'));
  resize();

  function setText(id, value) { $(id).textContent = value; }
  function colour(element, value) {
    element.classList.toggle('positive', finite(value) && value > 0);
    element.classList.toggle('negative', finite(value) && value < 0);
  }
  function pill(text, kind = '') {
    const span = document.createElement('span');
    span.className = 'badge ' + kind;
    span.textContent = text;
    return span;
  }
  function ageText(seconds) {
    if (!finite(seconds)) return '—';
    if (seconds < -5) return 'время в будущем';
    if (seconds < 60) return Math.max(0, Math.floor(seconds)) + ' с назад';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' мин назад';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' ч назад';
    return Math.floor(seconds / 86400) + ' дн назад';
  }

  function freshMarket() {
    const market = liveMarkets.get(symbol);
    const age = market ? Date.now() / 1000 - market.server_time : Infinity;
    return market?.ok && !marketError && age >= -5 && age <= 30 ? market : null;
  }
  function selectedTrade() { return state?.trades.find(trade => trade.id === selectedId) ?? null; }
  function valuation(trade) {
    if (trade.status === 'CLOSED') {
      return {netUsdt: trade.pnl_usdt, netPercent: trade.pnl_percent,
        grossPercent: trade.gross_pnl_percent, fees: trade.fees_usdt};
    }
    if (trade.status !== 'OPEN' || trade.symbol !== symbol || journalError) return null;
    const market = freshMarket();
    return market ? netPnl(trade, market.price) : null;
  }

  async function getJson(path, controller = new AbortController()) {
    const timeout = setTimeout(() => controller.abort(), 6500);
    try {
      const response = await fetch(path, {cache: 'no-store', signal: controller.signal});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Ответ сервера недоступен');
      return data;
    } finally { clearTimeout(timeout); }
  }

  function applyCandles(data, reset = false) {
    const key = symbol + '/' + mode;
    reset = reset || chartKey !== key;
    const old = reset ? [] : chartCandles;
    const oldLast = old.at(-1)?.time;
    const map = new Map(old.map(candle => [candle.time, candle]));
    let historicalChange = false;
    for (const candle of data.candles) {
      const previous = map.get(candle.time);
      if (previous && candle.time < oldLast && ['open', 'high', 'low', 'close', 'volume'].some(k => previous[k] !== candle[k])) historicalChange = true;
      map.set(candle.time, candle);
    }
    const merged = [...map.values()].sort((a, b) => a.time - b.time).slice(-5000);
    const range = chart.timeScale().getVisibleRange();
    const replace = reset || historicalChange || old[0]?.time !== merged[0]?.time;
    const volumeBar = candle => ({time: candle.time, value: candle.volume, color: candle.close >= candle.open ? '#26b99a42' : '#ec5c7742'});
    if (replace) {
      series.setData(merged);
      volume.setData(merged.map(volumeBar));
      if (!reset && range && !$('follow-toggle').checked) chart.timeScale().setVisibleRange(range);
    } else {
      for (const candle of merged) {
        if (candle.time >= oldLast) { series.update(candle); volume.update(volumeBar(candle)); }
      }
    }
    chartCandles = merged;
    chartKey = key;
    times = new Set(merged.map(candle => candle.time));
    if (reset) chart.timeScale().setVisibleLogicalRange({from: Math.max(0, merged.length - 110), to: merged.length + 6});
    if (mode === 'live' && $('follow-toggle').checked) chart.timeScale().scrollToRealTime();
    const latest = merged.at(-1);
    if (latest) updateOHLC(latest);
    updateDrawings();
  }

  function updateOHLC(candle) {
    setText('ohlc', 'O ' + priceText(candle.open) + '  H ' + priceText(candle.high) + '  L ' + priceText(candle.low) + '  C ' + priceText(candle.close));
  }
  chart.subscribeCrosshairMove(event => {
    const candle = event.seriesData.get(series);
    if (candle?.open !== undefined) updateOHLC(candle);
  });
  chart.subscribeClick(event => {
    if (typeof event.hoveredObjectId === 'string' && event.hoveredObjectId.startsWith('trade:')) {
      chooseTrade(event.hoveredObjectId.split(':')[1]);
    }
  });

  function updateDrawings() {
    if (!state) return;
    const displayed = state.trades.filter(trade => trade.symbol === symbol && trade.entry_price);
    const visibleMarkers = [];
    for (const trade of displayed) {
      const entryTime = candleTime(trade.opened_at), exitTime = candleTime(trade.closed_at);
      const size = trade.id === selectedId ? 2 : 1;
      if (times.has(entryTime)) visibleMarkers.push({time: entryTime, id: 'trade:' + trade.id + ':entry',
        position: trade.side === 'BUY' ? 'belowBar' : 'aboveBar', shape: trade.side === 'BUY' ? 'arrowUp' : 'arrowDown',
        color: trade.side === 'BUY' ? '#35d0a0' : '#ff6d7e', text: trade.side + ' #' + trade.ordinal, size});
      if (trade.status === 'CLOSED' && trade.exit_price && times.has(exitTime)) visibleMarkers.push({
        time: exitTime, id: 'trade:' + trade.id + ':exit', position: trade.side === 'BUY' ? 'aboveBar' : 'belowBar',
        shape: 'circle', color: finite(trade.pnl_usdt) ? (trade.pnl_usdt > 0 ? '#35d0a0' : '#ff6d7e') : '#b2bdd1',
        text: 'Выход #' + trade.ordinal, size});
    }
    visibleMarkers.sort((a, b) => a.time - b.time);
    setText('marker-count', 'Маркеры: ' + visibleMarkers.filter(marker => marker.id.endsWith(':entry')).length + ' входов · ' + visibleMarkers.filter(marker => marker.id.endsWith(':exit')).length + ' выходов');
    const signature = JSON.stringify(visibleMarkers);
    if (signature !== markerSignature) { markers.setMarkers(visibleMarkers); markerSignature = signature; }
    const trade = selectedTrade();
    const levelsKey = JSON.stringify([symbol, trade?.id, trade?.status, trade?.entry_price, trade?.exit_price,
      trade?.stop_price, trade?.take_price, chartCandles[0]?.time, chartCandles.at(-1)?.time]);
    if (levelsKey !== levelSignature) {
      priceLines.forEach(line => series.removePriceLine(line)); priceLines = [];
      connector.setData([]);
      if (trade?.symbol === symbol) {
        const add = (price, colour, title) => {
          if (finite(price) && price > 0) priceLines.push(series.createPriceLine({price, color: colour, lineWidth: 1,
            lineStyle: L.LineStyle.Dashed, axisLabelVisible: true, title}));
        };
        add(trade.entry_price, '#78a8ff', 'Вход #' + trade.ordinal);
        if (trade.status === 'OPEN') {
          add(trade.stop_price, '#ff6d7e', 'SL · сейчас'); add(trade.take_price, '#35d0a0', 'TP · сейчас');
        } else {
          add(trade.exit_price, '#edbc64', 'Выход #' + trade.ordinal);
          const from = candleTime(trade.opened_at), to = candleTime(trade.closed_at);
          if (finite(from) && finite(to) && from < to && times.has(from) && times.has(to) && trade.exit_price) {
            connector.setData([{time: from, value: trade.entry_price}, {time: to, value: trade.exit_price}]);
          }
        }
      }
      levelSignature = levelsKey;
    }
  }

  function renderDetails() {
    const trade = selectedTrade();
    const box = $('trade-details'); box.replaceChildren();
    const status = $('trade-status'); status.textContent = trade ? '#' + trade.ordinal + ' · ' + trade.status : '—';
    status.className = 'badge ' + (trade?.status === 'OPEN' ? 'blue' : '');
    if (!trade) { const p = document.createElement('p'); p.className = 'empty-small'; p.textContent = 'Нажми на сделку в таблице или на маркер.'; box.append(p); return; }
    const result = valuation(trade);
    const dl = document.createElement('dl');
    function pair(label, value, numeric = null) {
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = label; dd.textContent = value;
      if (numeric !== null) colour(dd, numeric);
      dl.append(dt, dd);
    }
    pair('Инструмент', trade.symbol + ' · ' + trade.side);
    pair('Вход UTC', utcText(trade.opened_at));
    pair('Цена входа', priceText(trade.entry_price));
    if (trade.status === 'CLOSED') { pair('Выход UTC', utcText(trade.closed_at)); pair('Цена выхода', priceText(trade.exit_price)); }
    else { pair('SL · текущие настройки', priceText(trade.stop_price)); pair('TP · текущие настройки', priceText(trade.take_price)); }
    pair('Маржа', finite(trade.margin) ? number(trade.margin) + ' USDT' : 'Не записана');
    pair('Плечо', finite(trade.leverage) ? '×' + number(trade.leverage, 1) : 'Не записано');
    pair('Комиссия за сторону', finite(trade.fee_rate) ? number(trade.fee_rate * 100, 3) + '%' : 'Не записана');
    pair(trade.status === 'OPEN' ? 'Оценка net' : 'PnL журнала', number(result?.netUsdt, 4, true) + (finite(result?.netUsdt) ? ' USDT' : ''), result?.netUsdt);
    pair('% от маржи', number(result?.netPercent, 4, true) + (finite(result?.netPercent) ? '%' : ''), result?.netPercent);
    if (finite(result?.fees)) pair('Комиссии · вход и выход', number(result.fees, 4) + ' USDT');
    if (trade.status === 'CLOSED') pair('Причина', trade.close_reason || 'Не записана');
    box.append(dl);
    const note = document.createElement('p'); note.className = 'detail-note';
    note.textContent = trade.status === 'OPEN' ? 'Net — оценка закрытия по последней цене с входной и выходной комиссиями.'
      : 'PnL взят из журнала. Исторические TP/SL в этом формате журнала не сохранялись.';
    if (trade.used_legacy_defaults) note.textContent += ' Для старой открытой позиции применены legacy-параметры, как у бота.';
    if (trade.problems.length) { note.classList.add('warn'); note.textContent += ' ' + trade.problems.join('; '); }
    box.append(note);
  }

  function renderTable() {
    if (!state) return;
    const filter = $('status-filter').value;
    const matches = state.trades.filter(trade => trade.symbol === symbol && (filter === 'ALL' || trade.status === filter))
      .sort((a, b) => (b.opened_at ?? 0) - (a.opened_at ?? 0) || b.ordinal - a.ordinal);
    const rows = matches.slice(0, 200);
    const data = rows.map(trade => ({trade, result: valuation(trade)}));
    const signature = JSON.stringify([selectedId, filter, symbol, data]);
    if (signature === tableSignature) return;
    tableSignature = signature;
    const body = $('trades-body'); body.replaceChildren();
    for (const {trade, result} of data) {
      const row = document.createElement('tr'); row.dataset.id = trade.id;
      row.classList.toggle('selected', trade.id === selectedId);
      const add = text => { const td = document.createElement('td'); td.textContent = text; row.append(td); return td; };
      add(String(trade.ordinal)); add(utcText(trade.opened_at));
      const side = add(''); side.append(pill(trade.side, trade.side === 'BUY' ? 'good' : 'bad'));
      const status = add(''); status.append(pill(trade.status, trade.status === 'OPEN' ? 'blue' : ''));
      add(priceText(trade.entry_price));
      add(priceText(trade.status === 'CLOSED' ? trade.exit_price : (freshMarket()?.price ?? null)));
      colour(add(number(result?.netUsdt, 4, true)), result?.netUsdt);
      colour(add(number(result?.netPercent, 4, true)), result?.netPercent);
      add(trade.status === 'OPEN' ? (trade.problems.length ? trade.problems.join('; ') : 'Открыта') : trade.close_reason || '—');
      body.append(row);
    }
    setText('table-title', 'Сделки · ' + symbol);
    setText('table-note', 'Показано ' + rows.length + ' из ' + matches.length + ' · обновление журнала каждые 2 секунды');
    $('table-empty').classList.toggle('visible', rows.length === 0);
    setText('table-empty', state.journal.exists ? 'Для этого инструмента и фильтра сделок пока нет.' : 'Ожидаем создания paper_trades.csv ботом.');
  }

  function renderPositions() {
    const positions = state?.trades.filter(trade => trade.status === 'OPEN') ?? [];
    setText('positions-badge', String(positions.length));
    const key = JSON.stringify([selectedId, positions.map(t => [t.id, t.symbol, t.side, t.ordinal])]);
    if (key === positionsSignature) return;
    positionsSignature = key;
    const box = $('positions-list'); box.replaceChildren();
    if (!positions.length) { const p = document.createElement('p'); p.className = 'empty-small'; p.textContent = 'В журнале нет открытых позиций.'; box.append(p); }
    for (const trade of positions) {
      const button = document.createElement('button'); button.className = 'position-button'; button.type = 'button';
      button.classList.toggle('selected', trade.id === selectedId);
      const title = document.createElement('span'); title.textContent = trade.symbol + ' · ' + trade.side;
      const id = document.createElement('small'); id.textContent = '#' + trade.ordinal;
      button.append(title, id); button.addEventListener('click', () => chooseTrade(trade.id)); box.append(button);
    }
  }

  function renderSignal() {
    const signal = state?.signals[symbol];
    setText('signal-direction', signal?.signal ?? '—');
    colour($('signal-direction'), signal?.signal === 'BUY' ? 1 : signal?.signal === 'SELL' ? -1 : 0);
    setText('signal-confidence', finite(signal?.confidence) ? 'Уверенность: ' + number(signal.confidence * 100, 2) + '%' : 'Нет достоверной записи');
    setText('signal-reason', signal?.reason ?? 'Сигнал не является открытой сделкой. Здесь появится последняя запись live_signals.csv.');
    const badge = $('signal-freshness');
    badge.textContent = signal ? (signal.fresh ? 'Свежий' : 'Устарел / неверен') : 'Нет записи';
    badge.className = 'badge ' + (signal?.fresh ? 'good' : 'warn');
    if (finite(signal?.timestamp)) badge.title = utcText(signal.timestamp) + ' UTC';
  }

  function render() {
    const market = freshMarket();
    const stale = liveMarkets.get(symbol);
    setText('instrument-title', symbol.replace(/USDT$/, ' / USDT'));
    setText('open-pnl-title', 'Оценка net · ' + symbol);
    setText('closed-pnl-title', 'Закрытый PnL журнала · ' + symbol);
    setText('last-price', priceText(market?.price));
    setText('market-age', market ? 'Bybit · ' + ageText(Date.now() / 1000 - market.server_time) : stale ? 'Последнее обновление: ' + ageText(Date.now() / 1000 - stale.server_time) : 'Ожидаем котировки Bybit');
    $('connection-dot').className = 'dot ' + (journalError ? 'error' : state ? 'ok' : '');
    setText('connection-text', journalError ? 'Журнал недоступен' : state ? 'Журнал читается автоматически' : 'Подключение к журналу…');
    const messages = [...(state?.warnings ?? [])];
    if (journalError) messages.unshift('Журнал: ' + journalError + '. Показана последняя прочитанная версия.');
    if (marketError) messages.push(marketError + '. Оценка открытого PnL приостановлена до свежих котировок.');
    if (historyError) messages.push(historyError);
    if (historyBusy) messages.push('Загружаем свечи около выбранной сделки…');
    const messageBox = $('messages'); messageBox.replaceChildren();
    for (const text of messages) { const p = document.createElement('p'); p.textContent = text; messageBox.append(p); }
    messageBox.classList.toggle('visible', messages.length > 0);
    const badge = $('view-badge'); badge.textContent = mode === 'live' ? 'Текущий рынок' : 'Архив сделки'; badge.className = 'badge ' + (mode === 'live' ? 'blue' : 'warn');
    setText('candle-state', mode === 'history' ? 'Исторические свечи · живой ценовой индикатор сверху продолжает обновляться'
      : market ? (market.forming ? 'Последняя свеча формируется · обновление 5 с' : 'Последняя свеча закрыта · обновление 5 с') : 'Котировки временно недоступны');
    if (!state) return;
    const allOpen = state.trades.filter(trade => trade.status === 'OPEN');
    const selectedOpen = allOpen.filter(trade => trade.symbol === symbol);
    const valued = selectedOpen.map(valuation);
    const known = valued.filter(v => v && finite(v.netUsdt));
    const totalOpen = known.reduce((sum, value) => sum + value.netUsdt, 0);
    const complete = selectedOpen.length === known.length && !journalError;
    setText('open-count', journalError ? '—' : String(allOpen.length));
    setText('open-count-note', 'По выбранному инструменту: ' + selectedOpen.length);
    setText('open-pnl', complete ? number(totalOpen, 4, true) + ' USDT' : '—'); colour($('open-pnl'), complete ? totalOpen : null);
    setText('open-pnl-note', complete ? 'Входная и предполагаемая выходная комиссии' : 'Оценено ' + known.length + ' из ' + selectedOpen.length + ' открытых позиций');
    const closed = state.trades.filter(trade => trade.symbol === symbol && trade.status === 'CLOSED');
    const closedKnown = closed.filter(trade => finite(trade.pnl_usdt));
    const closedSum = closedKnown.reduce((sum, trade) => sum + trade.pnl_usdt, 0);
    setText('closed-pnl', closedKnown.length === closed.length && !journalError ? number(closedSum, 4, true) + ' USDT' : '—');
    colour($('closed-pnl'), closedKnown.length === closed.length && !journalError ? closedSum : null);
    setText('closed-pnl-note', closed.length + ' закрытых · записанные результаты без пересчёта');
    setText('journal-info', 'paper_trades.csv · сохранён: ' + utcText(state.journal.modified_at) + ' UTC · строки: ' + state.journal.rows);
    renderPositions(); renderSignal(); renderDetails(); renderTable(); updateDrawings();
  }

  function syncSymbols() {
    const selector = $('symbol-select');
    const options = [...new Set([symbol, ...(state?.symbols ?? [])])].sort();
    if ([...selector.options].map(o => o.value).join('|') !== options.join('|')) {
      selector.replaceChildren(...options.map(value => { const option = document.createElement('option'); option.value = option.textContent = value; return option; }));
    }
    selector.value = symbol;
  }
  async function pollState() {
    if (stateBusy) return;
    stateBusy = true;
    try {
      state = await getJson('/api/state'); journalError = '';
      if (selectedId && !state.trades.some(trade => trade.id === selectedId)) selectedId = null;
      if (!selectedId) selectedId = state.trades.find(trade => trade.symbol === symbol && trade.status === 'OPEN')?.id ?? null;
      syncSymbols();
    } catch (error) { journalError = error.message; }
    finally { stateBusy = false; render(); }
  }
  async function pollMarket(force = false) {
    if (marketBusy && !force) return;
    if (force) marketController?.abort();
    const target = symbol, controller = new AbortController();
    marketController = controller; marketBusy = true;
    try {
      const data = await getJson('/api/market?symbol=' + encodeURIComponent(target), controller);
      liveMarkets.set(target, data);
      if (target === symbol) { marketError = ''; if (mode === 'live') applyCandles(data); }
    } catch (error) {
      if (target === symbol && marketController === controller) marketError = error.name === 'AbortError' ? 'Превышено время ожидания Bybit' : error.message;
    } finally {
      if (marketController === controller) { marketBusy = false; render(); }
    }
  }

  function focusTrade(trade) {
    const from = candleTime(trade.opened_at), to = candleTime(trade.closed_at) ?? chartCandles.at(-1)?.time;
    if (times.has(from) && finite(to)) {
      chart.timeScale().setVisibleRange({from: from - 18 * 3600, to: Math.max(to, from + 6 * 3600) + 18 * 3600});
    } else {
      historyError = 'Вход сделки находится вне загруженных 1000 часовых свечей. Проверь часовой пояс журнала или выбери более свежую сделку.';
    }
  }
  async function showHistory(trade) {
    const sequence = ++historySequence;
    historyBusy = true; historyError = ''; mode = 'history'; $('follow-toggle').checked = false; render();
    const end = Math.min(Date.now(), ((trade.closed_at ?? trade.opened_at) + 48 * 3600) * 1000);
    try {
      const data = await getJson('/api/market?symbol=' + encodeURIComponent(symbol) + '&end=' + Math.floor(end));
      if (sequence !== historySequence || selectedId !== trade.id) return;
      applyCandles(data, true); focusTrade(trade);
    } catch (error) { if (sequence === historySequence) historyError = error.message; }
    finally { if (sequence === historySequence) { historyBusy = false; render(); } }
  }
  function chooseSymbol(value, keepSelection = false) {
    if (value === symbol) return;
    historySequence++; historyBusy = false; symbol = value; mode = 'live'; historyError = ''; marketError = '';
    if (!keepSelection) selectedId = state?.trades.find(trade => trade.symbol === symbol && trade.status === 'OPEN')?.id ?? null;
    $('follow-toggle').checked = true;
    chartCandles = []; times.clear(); chartKey = ''; markerSignature = ''; levelSignature = '';
    series.setData([]); volume.setData([]); connector.setData([]); markers.setMarkers([]);
    priceLines.forEach(line => series.removePriceLine(line)); priceLines = [];
    const cached = freshMarket(); if (cached) applyCandles(cached, true);
    syncSymbols(); render(); pollMarket(true);
  }
  function chooseTrade(id) {
    const trade = state?.trades.find(item => item.id === id);
    if (!trade) return;
    selectedId = id; historyError = '';
    if (trade.symbol !== symbol) chooseSymbol(trade.symbol, true);
    if (trade.status === 'CLOSED') {
      $('follow-toggle').checked = false;
      if (mode === 'live' && times.has(candleTime(trade.opened_at)) && times.has(candleTime(trade.closed_at))) focusTrade(trade);
      else showHistory(trade);
    } else {
      mode = 'live'; historySequence++; historyBusy = false;
      const market = freshMarket(); if (market) applyCandles(market, chartKey !== symbol + '/live');
    }
    render();
  }

  $('symbol-select').addEventListener('change', event => chooseSymbol(event.target.value));
  $('status-filter').addEventListener('change', renderTable);
  $('trades-body').addEventListener('click', event => { const row = event.target.closest('tr[data-id]'); if (row) chooseTrade(row.dataset.id); });
  $('live-button').addEventListener('click', () => {
    historySequence++; historyBusy = false; historyError = ''; mode = 'live'; $('follow-toggle').checked = true;
    selectedId = state?.trades.find(trade => trade.symbol === symbol && trade.status === 'OPEN')?.id ?? null;
    const data = freshMarket(); if (data) applyCandles(data, true);
    render(); pollMarket(true);
  });
  $('follow-toggle').addEventListener('change', () => { if ($('follow-toggle').checked && mode === 'live') chart.timeScale().scrollToRealTime(); });
  pollState(); pollMarket();
  setInterval(pollState, 2000);
  setInterval(pollMarket, 5000);
  setInterval(() => { if (!freshMarket()) render(); }, 1000);
}

if (typeof document !== 'undefined') start();
