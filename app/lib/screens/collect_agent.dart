import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:share_plus/share_plus.dart';

import '../api.dart';
import '../money.dart';
import '../recorder.dart';
import '../theme.dart';
import '../widgets.dart';

/// "Who should I chase today?" — the Collect agent reads the book with read-only
/// tools and proposes payment-link reminders. Nothing is created until the
/// shopkeeper ticks the cards and taps the button; amounts always come from the book.
class CollectAgentScreen extends StatefulWidget {
  const CollectAgentScreen({super.key, this.initialText});
  final String? initialText;

  @override
  State<CollectAgentScreen> createState() => _CollectAgentScreenState();
}

class _CollectAgentScreenState extends State<CollectAgentScreen> {
  static const _suggestions = [
    'Who should I chase today?',
    'Links for everyone over 60 days',
    '¿A quién le cobro hoy?',
  ];

  final _text = TextEditingController();
  bool _thinking = false, _sending = false;
  String? _heard, _say, _error;
  List<Map<String, dynamic>> _cards = [];
  List<String> _dropped = [];
  final Set<int> _picked = {};
  final _scroll = ScrollController();

  /// Bring the answer into view — it appears below the mic and suggestions.
  void _reveal() => WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients) _scroll.animateTo(380, duration: const Duration(milliseconds: 350), curve: Curves.easeOut);
      });
  List<Map<String, dynamic>>? _sent;

  @override
  void initState() {
    super.initState();
    if (widget.initialText != null) {
      _text.text = widget.initialText!;
      WidgetsBinding.instance.addPostFrameCallback((_) => _plan(widget.initialText!));
    }
  }

  @override
  void dispose() {
    _scroll.dispose();
    _text.dispose();
    super.dispose();
  }

  void _show(Map<String, dynamic> r) {
    final cards = (r['cards'] as List).cast<Map<String, dynamic>>();
    setState(() {
      _heard = r['heard'] as String?;
      _say = r['say'] as String?;
      _cards = cards;
      _dropped = (r['dropped'] as List? ?? []).cast<String>();
      _picked
        ..clear()
        ..addAll(cards.map((c) => c['party_id'] as int));
      _sent = null;
    });
  }

  Future<void> _run(Future<Map<String, dynamic>> Function() call) async {
    setState(() => (_thinking = true, _error = null, _sent = null));
    try {
      _show(await call());
      _reveal();
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _thinking = false);
    }
  }

  Future<void> _plan(String text) => _run(() => Api.instance.collectPlan(text));
  Future<void> _voice(Uint8List wav) => _run(() => Api.instance.collectPlanVoice(wav));

  Future<void> _send() async {
    final chosen = [
      for (final c in _cards)
        if (_picked.contains(c['party_id'])) c,
    ];
    if (chosen.isEmpty) return;
    setState(() => _sending = true);
    try {
      final sent = await Api.instance.collectSend([
        for (final c in chosen) {'party_id': c['party_id'], 'amount_minor': c['amount_minor'], 'language': c['language']},
      ]);
      if (mounted) setState(() => (_sent = sent, _cards = []));
      _reveal();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = context.c;
    return Scaffold(
      appBar: innerBar('Get paid'),
      body: ListView(
        controller: _scroll,
        padding: const EdgeInsets.fromLTRB(20, 4, 20, 28),
        children: [
          Text(
            'Ask who to chase, or name a customer. You get reminder cards with PayPal links — nothing is sent until you say so.',
            style: TextStyle(color: c.inkSoft, fontSize: 15),
          ),
          const SizedBox(height: 16),
          Center(child: HoldToSpeak(onAudio: _voice, size: 92)),
          Center(
            child: Text(
              'Hold to speak',
              style: TextStyle(fontWeight: FontWeight.w700, color: c.ink, fontSize: 16),
            ),
          ),
          const SizedBox(height: 14),
          TextField(
            controller: _text,
            textInputAction: TextInputAction.send,
            onSubmitted: (v) => v.trim().isEmpty || _thinking ? null : _plan(v.trim()),
            decoration: InputDecoration(
              hintText: 'Or type, e.g. "Send Maria a payment link"',
              suffixIcon: IconButton(
                icon: Icon(Icons.arrow_upward_rounded, color: c.accent),
                onPressed: () => _text.text.trim().isEmpty || _thinking ? null : _plan(_text.text.trim()),
              ),
            ),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final s in _suggestions)
                ActionChip(
                  label: Text(s),
                  onPressed: _thinking
                      ? null
                      : () {
                          _text.text = s;
                          _plan(s);
                        },
                ),
            ],
          ),
          const SizedBox(height: 18),
          if (_thinking) const Padding(padding: EdgeInsets.all(24), child: Working('Reading your book…')),
          if (_error != null) Text(_error!, style: TextStyle(color: c.owes, fontSize: 15)),
          if (!_thinking && _heard != null && _sent == null) ..._planView(),
          if (_sent != null) ..._sentView(),
        ],
      ),
    );
  }

  List<Widget> _planView() {
    final c = context.c;
    return [
      Text(
        'You said: “$_heard”',
        style: TextStyle(color: c.inkFaint, fontStyle: FontStyle.italic),
      ),
      if ((_say ?? '').isNotEmpty) ...[
        const SizedBox(height: 8),
        Text(_say!, style: TextStyle(fontSize: 16.5, color: c.ink, height: 1.35)),
      ],
      const SizedBox(height: 12),
      for (final card in _cards) _cardView(card),
      for (final d in _dropped)
        Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: Row(
            children: [
              Icon(Icons.info_outline_rounded, size: 18, color: c.doubt),
              const SizedBox(width: 8),
              Expanded(
                child: Text('Skipped: $d', style: TextStyle(color: c.inkSoft)),
              ),
            ],
          ),
        ),
      if (_cards.isNotEmpty) ...[
        const SizedBox(height: 10),
        FilledButton.icon(
          onPressed: _picked.isEmpty || _sending ? null : _send,
          icon: _sending
              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white))
              : const Icon(Icons.link_rounded),
          label: Text('Create ${_picked.length} PayPal link${_picked.length == 1 ? '' : 's'}'),
        ),
        const SizedBox(height: 6),
        Text(
          'PayPal sandbox. Amounts come from your book, never from the AI.',
          textAlign: TextAlign.center,
          style: TextStyle(color: c.inkFaint, fontSize: 12.5),
        ),
      ],
    ];
  }

  Widget _cardView(Map<String, dynamic> card) {
    final c = context.c;
    final cur = card['currency'] as String;
    final id = card['party_id'] as int;
    final on = _picked.contains(id);
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Panel(
        padding: const EdgeInsets.fromLTRB(8, 12, 16, 12),
        onTap: () => setState(() => on ? _picked.remove(id) : _picked.add(id)),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Checkbox(value: on, activeColor: c.accent, onChanged: (_) => setState(() => on ? _picked.remove(id) : _picked.add(id))),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(card['party'] as String, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16.5)),
                      ),
                      Text(formatMoney(card['amount_minor'] as int, cur), style: display(context, 19, color: c.owes)),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Text(
                    [
                      card['partial'] == true ? 'Part of ${formatMoney(card['balance_minor'] as int, cur)} owed' : 'Full balance',
                      card['language'],
                    ].join(' · '),
                    style: TextStyle(color: c.inkFaint, fontSize: 13.5),
                  ),
                  if ((card['why'] as String? ?? '').isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(card['why'] as String, style: TextStyle(color: c.inkSoft, fontSize: 14)),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  List<Widget> _sentView() {
    final c = context.c;
    return [
      Text('Links ready', style: display(context, 22)),
      const SizedBox(height: 4),
      Text('Share each message yourself. When a customer pays, it shows up in your Payment inbox.', style: TextStyle(color: c.inkSoft)),
      const SizedBox(height: 12),
      for (final s in _sent!) _sentCard(s),
    ];
  }

  Widget _sentCard(Map<String, dynamic> s) {
    final c = context.c;
    final req = Map<String, dynamic>.from(s['request'] as Map);
    final msg = s['message'] as String;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Panel(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(s['party'] as String, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16.5)),
                ),
                Text(
                  formatMoney((req['due_minor'] ?? req['amount_minor']) as int, req['currency'] as String),
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: c.paper, borderRadius: BorderRadius.circular(14)),
              child: SelectableText(msg, style: TextStyle(color: c.ink, height: 1.35)),
            ),
            const SizedBox(height: 6),
            Text(
              s['used_model'] == true
                  ? 'Written by AI in ${s['language']}. Amount and link inserted by the app.'
                  : 'Standard message. Amount and link inserted by the app.',
              style: TextStyle(color: c.inkFaint, fontSize: 12.5),
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: FilledButton.icon(
                    onPressed: () => SharePlus.instance.share(ShareParams(text: msg)),
                    icon: const Icon(Icons.share_outlined, size: 19),
                    label: const Text('Share'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () async {
                      await Clipboard.setData(ClipboardData(text: req['link_url'] as String));
                      if (mounted) showSnack(context, 'Payment link copied');
                    },
                    icon: const Icon(Icons.link_rounded, size: 19),
                    label: const Text('Copy link'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
