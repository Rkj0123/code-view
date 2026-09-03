#pragma once

#include <QStringView>

#include "Edge.h"
#include "NodeKind.h"

NodeKind legacyNodeKind(QStringView kind);
Edge::EdgeType legacyEdgeKind(QStringView kind);
