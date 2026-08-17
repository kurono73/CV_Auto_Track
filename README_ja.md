# CV Auto Track

[English](README.md) | [日本語](README_ja.md)


## 概要

CV Auto Track は、Blender の Movie Clip Editor に OpenCV を利用した高速な自動トラッキング機能を追加します。

シンプルなプリセットベースのワークフローを想定しており、フッテージを開き、プリセットを選択して自動トラッキングを実行できます。2D特徴点を検出し、クリップ全体を追跡し、弱い候補をフィルタリングし、画面内の分布を整えたうえで、Blender標準の Movie Tracking マーカーとして結果を書き込みます。

## 主な機能

- **高速な OpenCV 自動トラッキング:** 多数の2Dトラッキングマーカーを高速に生成します。
- **シンプルなプリセット:** Fast、Dynamic、High Motion など、フッテージに合わせたプリセットから開始できます。
- **ワンクリックでトラックからSolveまで:** 検出、トラッキング、Solve Setup、Solve、Refine を1つのコマンドで実行できます。
- **自動フィルタリング:** 短いトラック、重複、不安定なトラック、Solve外れ値を除去します。
- **均等な画面カバレッジ:** 一部に集中しすぎないよう、マーカーを画面全体へ分布させます。
- **マスク対応トラッキング:** マスク領域を避け、禁止領域へ入ったトラックを終了します。
- **キャッシュを利用した追加トラッキング:** フル解析をやり直さず、キャッシュ済みOpenCV候補から追加の2Dトラックを生成できます。
- **Blender標準出力:** `AT_0001`、`AT_0002` のような通常の Movie Clip Editor トラックとして書き込みます。

## 推奨フッテージ

CV Auto Track は、目立つテクスチャがあり、実際のカメラ移動が含まれるフッテージで最も効果を発揮します。

- 安定した環境を含むカメラトラッキングショット
- 建築、街並み、室内など、コーナー特徴が多い環境
- パララックスが見えるドリー、ドローン、ハンドヘルド、パンショット
- `Dynamic` プリセットを使う長尺または視点変化の大きいショット
- `High Motion` プリセットを使う高速カメラ移動

## 難しいフッテージ

一部のショットでは、マスク、別プリセット、手動クリーンアップが必要になる場合があります。

- 強いモーションブラーやデフォーカス
- 大きな前景オクルーダー
- マスクされていない人物、車両、その他の動体
- 反射、透明面、繰り返し模様、水、煙、葉、空
- テクスチャの少ない壁や平坦な面

## 現在のワークフロー

1. Blender の **Movie Clip Editor** でフッテージを開き、通常どおりクリップ設定を行います。
2. Toolbar から `CV Auto Track` パネルを開きます。
3. `Fast`、`Dynamic`、`High Motion` などのトラッキングプリセットを選択します。
4. *(任意)* `Solve Setup` を開き、キーフレーム、Tripod Motion、Focal Length、Distortion Refine、Full Auto Refine Passes を確認します。
5. *(任意)* `Use Mask` を有効にして、動体やトラッキング禁止領域を除外します。
6. *(任意)* 生成マーカー数を増減したい場合のみ `Density` を調整します。
7. **`Run Auto Track`** をクリックします。
8. 生成されたトラックとカメラSolve結果を確認します。
9. 必要に応じて、不良トラックを手動削除し、Solve設定を調整するか、Blender標準のトラッキングフローで手動トラックを追加します。
10. 調整後、必要に応じて `Solve` または `Solve & Refine` を再実行します。

Solveを行わずトラック生成だけ行いたい場合は、`Generate Tracks` を使用します。

> - `Filter` システムは多くの信頼性の低いトラックを除去しますが、再投影誤差はカメラSolve結果に依存するため、再投影誤差のみではフィルタリングしません。全体のSolve Errorが低くても、2.0 pxを超える個別トラックが残る場合があります。Dope Sheetを確認し、必要に応じて問題のあるトラックを手動で削除するか、Blender標準の `Solve > Clean Up` ツールで高エラートラックを除外してください。
> - `Generate Tracks` を先に実行して、動体へ追従しているトラックやその他の問題トラックを目視で削除してからSolveすることで、品質を高めつつマスク作業を省略できる場合があります。
> - **CV Auto Track は Blender 標準のトラッキングツールと併用することを前提に設計されています。** 必要に応じて手動トラックを追加し、Blender標準のトラッキングワークフローで難しいショットを補完してください。
> - **Proxy Fallback:** OpenEXR などOpenCVが直接読み込めないフッテージ形式では、CV Auto Track は Blender の100% Proxyを自動作成して使用するか、既存Proxyを再利用します。既存Proxyを使用する場合は、トラッキング精度低下を避けるため `Quality` を高く設定することを推奨します。
> - **Protected Tracks:** 選択中のトラックは `Solve & Refine` のFilter処理から除外されるため、重要なトラックを保護できます。

## メインコマンド

- **Run Auto Track:** 特徴点検出、トラッキング、候補フィルタリング、BlenderマーカーへのBake、必要に応じたKeyframe A/B設定、BlenderカメラSolve、Solve Refineまでを実行します。
- **Generate Tracks:** 検出、トラッキング、フィルタリング、分布調整、マーカーBakeのみを実行します。
  - **+ Add Tracks:** 最新のキャッシュ済みOpenCV候補から、控えめな数の追加2Dトラックを生成します。互換性のあるトラッキング処理後のみ有効になります。
- **Density:** 生成するマーカー量を調整します。低い値では軽量なSolveセット、高い値ではより密なカバレッジになります。
- **Solve Setup:** Auto Keyframe A/B、Tripod Motion、Camera Focal設定、Distortion Refine、Full Auto Refine Passes、Bake Marker Sizeなど、一般的なカメラSolve設定を1つのダイアログで開きます。
  - **Auto Keyframe A/B:** 安定したSolve用キーフレームを自動選択し、Blender標準のKeyframe Selectionとの重複動作を避けるためそれを無効化します。
  - **Auto Scene Setup:** Solve後にアクティブシーンカメラを自動セットアップし、必要に応じてCamera Solver、Clip Background、Undistorted表示を設定します。
  - **Full Auto Refine Passes:** Run Auto Track が実行するSolve-Refine Pass数を設定します。
  - **Bake Marker Size:** 生成されるBlenderマーカーのPatternおよびSearch Areaサイズを設定します。
- **Solve:** Add-on UIからBlender標準のカメラSolveを実行します。
- **Solve & Refine:** Solveを実行し、高エラーまたは動きに不整合のあるトラックを制御されたPassで除去します。
- **Analyze Solve:** Solve自体は変更せず、Solve外れ値候補を選択またはレポートします。

ForwardおよびAutoトラッキング時は、進捗表示とキャンセル応答性を保つため、検出/トラッキング処理をチャンク単位で実行します。BlenderのSolveおよびRefineはBlender側の処理のため、実行中にUIが一時停止する場合があります。

Radial Distortion Refineが有効な場合、CV Auto Track はSolve/Refine前にDistortion値をリセットし、クリーンな状態からSolveを開始します。

## プリセット

- **Fast:** 高速な汎用プリセット。通常のフッテージでは最初の選択肢として適しています。
- **Dynamic:** 長尺、パン、ドリー、視点が大きく変化するショット向けです。
- **High Motion:** 高速なカメラ移動、急なパン、フレーム間移動量が大きいショット向けです。
- **Balanced:** Fastより解析量を増やした汎用プリセットです。
- **Sensitive:** 低コントラストや弱いテクスチャのフッテージ向けに、より許容的な検出を行います。
- **Detailed:** フル解像度でより密な解析を行い、速度よりも詳細なトラッキングを優先します。

プリセット選択時に設定は即時反映されます。別途Applyボタンはありません。

ヘッダーのプリセットメニューはBlender標準のPreset Systemを使用します。独自のCV Auto Track設定を保存・再利用できます。MovieClipおよびMaskのDatablock Pointerはプリセットには保存されません。

## Filterプリセット

- **Lenient:** より多くのトラックを残し、緩やかな除外を行います。難しいフッテージやカバレッジ不足時に有効です。
- **Standard:** デフォルトの汎用クリーンアップ/Refineバランスです。
- **Strict:** 密で安定したカバレッジがあるフッテージで、より積極的に除外します。

Filterプリセットは候補クリーンアップとSolve-Refine時の除外に影響します。トラッキング方向や検出密度は変更しません。

## トラッキング方向

- **Forward:** 選択範囲の先頭フレームから末尾へ追跡します。最も高速なモードです。
- **Backward:** 選択範囲の末尾から先頭へ追跡します。
- **Both:** 範囲中央から両方向へ追跡します。
- **Current:** 現在のClip Frameをアンカーとして両方向へ追跡します。
- **Auto:** ForwardとBackwardを別Passで実行します。視点変化のあるショットで、一般的に追加コストが少ないままカバレッジを改善できるためデフォルトです。

Backward、Both、Current、およびBlenderのSolve/Refine段階では、ForwardやAutoよりUI応答が遅れる場合があります。

## Track Setup

Track Setupでは、解析対象フレームとOpenCVがフッテージを読み込む方法を制御します。

- **Frame Range:** 処理するClip範囲を選択します。通常はClip Full Rangeを使用し、短い範囲でテストしたい場合はCustom Rangeを使用します。
- **Direction:** トラッキング方向を選択します。広いカバレッジには `Auto`、最速処理には `Forward` を使用します。
- **Analysis Scale:** 一時的なOpenCV解析解像度を設定します。低い値は高速で、高い値はより細かい特徴を検出できます。
- **Use Mask:** マスク対応の検出とトラッキングを有効にします。動体や禁止領域を避けたい場合に使用します。

Advanced Modeでは、最小解析解像度、フレームキャッシュサイズ、その他の技術設定を追加で利用できます。

## Track Modes

`Mode` は既存のCV Auto Trackマーカーをどのように扱うかを制御します。

- **Auto Reuse:** デフォルト。Generate Tracksは既存の `AT_` トラックを置き換え、Run Auto Trackは既存の `AT_` トラックを再利用して検出処理をスキップします。
- **Replace:** 新しいトラックを生成する前に、既存の `AT_` トラックを削除します。
- **Add New:** 既存の `AT_` トラックを残したまま、追加の生成セットを加えます。

## マスク

動体や禁止領域を避けたい場合は `Use Mask` を有効にします。

Mask Source:

- **Blender Mask:** アクティブなClip Editor Mask、または選択したBlender Mask Datablockを使用します。
- **External MovieClip:** 白黒またはAlpha MaskをBlender MovieClipとして読み込んで使用します。

Mask Mode:

- **White Area to Exclude:** 白いピクセルを禁止領域として扱います。
- **White Area to Track:** 白いピクセルのみを許可領域として扱います。

Mask処理は検出とトラッキングの両方に適用されます。トラックが禁止マスク領域へ入るか、マスク境界を横切ると、そのトラックはフレーム端に到達した場合と同様に終了します。

External Mask Clipはアクティブフッテージ設定と同期できます。Mask DurationがアクティブClipと異なる場合、UIに警告が表示されます。

## BakeされたTrackの詳細

CV Auto Track は通常のBlender Movie Tracking Markerを書き込みます。生成されたトラックはMovie Clip Editor標準ツールで選択、非表示、編集、Solve、削除できます。

未トラッキング範囲はDisabled Marker SpanとしてBakeされるため、`Viewport Overlays` > `Show Disabled` で非アクティブ範囲をきれいに非表示にできます。

Status Lineには最終的なボタン押下から完了までの時間が表示されます。例: `Completed in 5.61s, 294 tracks`

## Advanced Mode

Advanced Modeでは、難しいフッテージやテスト向けの低レベル設定を利用できます。

- **Track Setup:** Frame Range、Direction、Analysis Scale、Cache Size、Mask設定。
- **Distribution:** GridとMarker Coverageの挙動。
- **Detection:** Maximum Features、Quality、Spacing、Block Size、Edge MarginなどのOpenCV Detector設定。
- **Tracking:** Window Size、Pyramid Levels、Motion Limit、Forward-Backward CheckなどのLucas-Kanade Optical Flow設定。
- **Filtering:** Length、Duplicate、Validity、RANSAC関連のクリーンアップ閾値。
- **Refine Settings:** Solve-Refine閾値、Protection Option、Outlier挙動。
- **Existing Tracks:** ユーザー作成トラックや既存 `AT_` トラックの保護・再利用設定。

`Auto Scale Pixel Parameters` はデフォルトで有効です。ピクセルベースの設定は有効解析解像度に応じて内部的にスケーリングされるため、FHD、4K、異なるAnalysis Scaleでもプリセット挙動をより一貫させます。

`SIFT`、`ORB`、`FAST` などの実験的Detector OptionもAdvanced Modeで利用できます。`Shi-Tomasi` はデフォルトであり、高速なLucas-Kanade Trackingには通常最も適しています。

---

# よくある質問

- **カメラSolveが正しくない、または不安定です。**  
  Solve前にカメラ設定が正しいことを確認してください。  
  **Auto Keyframe A/B** が不適切なキーフレームを選択している場合は無効化し、別の **Keyframe A** と **Keyframe B** を手動指定して再Solveしてください。

- **Focal Length または Radial Distortion が正しく推定されません。**  
  CV Auto Track はカメラキャリブレーションにBlender標準のCamera Solverを使用します。  
  フッテージ、選択された **Keyframe A/B**、初期カメラパラメータによっては、**Focal Length** や **Radial Distortion** が正確に推定されない場合があります。  
  別のKeyframeを選ぶか、より適切な初期値を指定してください。

- **良いトラックまで多く削除されてしまいます。**  
  **Run Auto Track** と **Solve & Refine** は、選択中の **Filter** 設定に基づいて高エラートラックを自動削除します。  
  生成されたトラックをすべて残したい場合:
  - **Generate Tracks** の後にBlender標準の **Solve** を使用します。
  - またはSolve前に **Filter** 設定を調整します。
  - 選択中のトラックは `Solve & Refine` のFilter処理から除外されます。

- **処理が非常に遅いです。**  
  処理時間は以下の要因によって変わります:
  - 高いソース解像度
  - 高い **Density** 値
  - 長尺フッテージ
  - **Detailed** プリセットの使用

  参考として、**Full HD・200フレーム** のClipは、ハードウェアにもよりますが **Fast** プリセットで通常 **20秒未満** で完了します。

- **どのプリセットでもトラックが生成されません。**  
  フッテージが自動トラッキングに適していない可能性があります。  
  CV Auto Track は以下のようなフッテージで最も効果を発揮します:
  - 視認できるテクスチャと特徴の多い表面
  - 実際のカメラ移動
  - 良好な画質
  - 安定したライティング
  - モーションブラーやデフォーカスが少ないこと

## ライセンス

CV Auto Track は GPL-3.0-or-later ライセンスです。OpenCV は Apache-2.0 ライセンスです。
