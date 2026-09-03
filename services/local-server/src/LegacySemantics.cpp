#include "LegacySemantics.h"

NodeKind legacyNodeKind(QStringView kind)
{
    if (kind == u"file")
        return NODE_FILE;
    if (kind == u"module")
        return NODE_MODULE;
    if (kind == u"package")
        return NODE_PACKAGE;
    if (kind == u"class")
        return NODE_CLASS;
    if (kind == u"function")
        return NODE_FUNCTION;
    if (kind == u"method")
        return NODE_METHOD;
    return NODE_SYMBOL;
}

Edge::EdgeType legacyEdgeKind(QStringView kind)
{
    if (kind == u"contains")
        return Edge::EDGE_MEMBER;
    if (kind == u"imports")
        return Edge::EDGE_IMPORT;
    if (kind == u"calls" || kind == u"constructs" || kind == u"api_calls")
        return Edge::EDGE_CALL;
    if (kind == u"inherits")
        return Edge::EDGE_INHERITANCE;
    if (kind == u"decorates")
        return Edge::EDGE_ANNOTATION_USAGE;
    if (kind == u"type_uses")
        return Edge::EDGE_TYPE_USAGE;
    if (kind == u"reads" || kind == u"writes" || kind == u"test_covers")
        return Edge::EDGE_USAGE;
    return Edge::EDGE_UNDEFINED;
}
